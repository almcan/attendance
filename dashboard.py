#!/usr/bin/env python3
"""
FeliCa 出席確認システム — Web ダッシュボード
=============================================
学生の在席状況をリアルタイムで表示する Web ダッシュボード。
Server-Sent Events (SSE) で出席変更を即座にブラウザへ反映。

使い方:
  .venv/bin/python dashboard.py
  ブラウザで http://localhost:5000 を開く
"""

import csv
import json
import queue
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

# ─── 定数 ───────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
STUDENTS_CSV = BASE_DIR / "students.csv"
ATTENDANCE_DIR = BASE_DIR / "attendance"

app = Flask(__name__)

# ─── SSE (Server-Sent Events) ──────────────────────────────

_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()


def notify_clients():
    """全 SSE クライアントに更新を通知する。"""
    data = _build_status_data()
    message = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(message)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


# ─── データ読み込み ─────────────────────────────────────────

# 座席表レイアウト（外部ファイルから読み込み）
SEATING_JSON = BASE_DIR / "seating.json"


def load_seating_layout() -> list:
    """seating.json から座席レイアウトを読み込む。"""
    if not SEATING_JSON.exists():
        print(f"[警告] {SEATING_JSON} が見つかりません。")
        return []
    with open(SEATING_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def load_students() -> list:
    """students.csv を読み込み、学生リストを返す。"""
    students = []
    if not STUDENTS_CSV.exists():
        return students
    with open(STUDENTS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            students.append({
                "idm": row["idm"].strip().upper(),
                "name": row["name"].strip(),
            })
    return students


def get_student_status(name: str) -> dict:
    """学生個人のCSVから最新のステータスを取得する（日付をまたいでも保持）。"""
    filepath = ATTENDANCE_DIR / f"{name}.csv"
    status = None
    timestamp = None

    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                status = row.get("status", "出席").strip()
                timestamp = row.get("timestamp", "").strip()

    return {"status": status, "timestamp": timestamp}


def match_student_to_seat(seat_name: str, students: list) -> dict | None:
    """座席名から students.csv の学生を検索する。"""
    for s in students:
        # 姓で照合
        clean_seat = seat_name.replace("(", "").replace(")", "").replace("（", "").replace("）", "")
        if s["name"].startswith(clean_seat) or clean_seat in s["name"]:
            return s
    return None


def _build_status_data() -> dict:
    """全学生の在席状況データを構築する。"""
    students = load_students()
    today = datetime.now().strftime("%Y年%m月%d日")
    now = datetime.now().strftime("%H:%M:%S")

    # 座席レイアウトにステータスを付与
    layout_data = []
    present_count = 0
    total_seats = 0

    for group in load_seating_layout():
        group_data = {"color": group["color"], "rows": []}
        for row in group["seats"]:
            row_data = []
            for seat_name in row:
                if seat_name is None:
                    row_data.append(None)
                    continue

                total_seats += 1
                student = match_student_to_seat(seat_name, students)

                if student:
                    info = get_student_status(student["name"])
                    seat_data = {
                        "seat_name": seat_name,
                        "full_name": student["name"],
                        "idm": student.get("idm", ""),
                        "status": info["status"],
                        "timestamp": info["timestamp"],
                    }
                    if info["status"] == "出席":
                        present_count += 1
                else:
                    seat_data = {
                        "seat_name": seat_name,
                        "full_name": None,
                        "status": None,
                        "timestamp": None,
                    }

                row_data.append(seat_data)
            group_data["rows"].append(row_data)
        layout_data.append(group_data)

    return {
        "date": today,
        "time": now,
        "total": total_seats,
        "present": present_count,
        "layout": layout_data,
        "unknown_tap": unknown_tap_counter["count"],
    }


# ─── API ────────────────────────────────────────────────────

@app.route("/")
def index():
    """ダッシュボードのメインページ。"""
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    """全学生の在席状況を JSON で返す。"""
    return jsonify(_build_status_data())


@app.route("/api/stream")
def api_stream():
    """SSE ストリームエンドポイント。出席変更時にリアルタイム通知。"""
    def event_stream():
        q = queue.Queue(maxsize=50)
        with _sse_lock:
            _sse_clients.append(q)
        try:
            # 接続直後に現在のステータスを送信
            data = _build_status_data()
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            # 以降は変更通知を待つ
            while True:
                try:
                    message = q.get(timeout=30)
                    yield message
                except queue.Empty:
                    # keepalive
                    yield ": keepalive\n\n"
        finally:
            with _sse_lock:
                if q in _sse_clients:
                    _sse_clients.remove(q)

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/register", methods=["POST"])
def api_register():
    """ブラウザから学生を登録する。"""
    data = request.get_json()
    name = data.get("name", "").strip()
    idm = data.get("idm", "").strip()

    if not name:
        return jsonify({"error": "氏名は必須です"}), 400

    # students.csv に追記
    if not STUDENTS_CSV.exists():
        with open(STUDENTS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["idm", "name"])

    with open(STUDENTS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([idm, name])

    # 全クライアントに通知
    notify_clients()

    return jsonify({"ok": True})


# ─── IDm 再登録 & 一時停止 & IDmキャプチャ ──────────────────
# 未登録カードタップのカウンター
unknown_tap_counter = {"count": 0}

# 「次のカードタッチでこの学生の IDm を更新する」フラグ
# paused: モーダル表示中は出席登録を停止
pending_reassign = {"name": None, "paused": False}

# 新規登録時のIDmキャプチャ
pending_capture = {"active": False, "idm": None}


@app.route("/api/pause", methods=["POST"])
def api_pause():
    """出席登録を一時停止する（モーダル表示中）。"""
    pending_reassign["paused"] = True
    return jsonify({"ok": True})


@app.route("/api/pause", methods=["DELETE"])
def api_resume():
    """出席登録を再開する（モーダル閉じた時）。"""
    pending_reassign["paused"] = False
    return jsonify({"ok": True})


@app.route("/api/capture", methods=["POST"])
def api_capture_start():
    """IDmキャプチャを開始する（登録モーダル用）。"""
    pending_capture["active"] = True
    pending_capture["idm"] = None
    return jsonify({"ok": True})


@app.route("/api/capture", methods=["GET"])
def api_capture_status():
    """キャプチャされたIDmを取得する。"""
    return jsonify({"idm": pending_capture["idm"]})


@app.route("/api/capture", methods=["DELETE"])
def api_capture_cancel():
    """IDmキャプチャをキャンセルする。"""
    pending_capture["active"] = False
    pending_capture["idm"] = None
    return jsonify({"ok": True})


@app.route("/api/reassign", methods=["POST"])
def api_reassign():
    """IDm 再登録を開始する。次のカードタッチで IDm を更新。"""
    data = request.get_json()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "氏名は必須です"}), 400
    pending_reassign["name"] = name
    return jsonify({"ok": True, "message": f"カードをタッチしてください"})


@app.route("/api/reassign", methods=["DELETE"])
def api_cancel_reassign():
    """IDm 再登録をキャンセルする。"""
    pending_reassign["name"] = None
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("=" * 50)
    print("  出席ダッシュボード起動中...")
    print("  http://localhost:5000 でアクセス")
    print("  終了: Ctrl+C")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False)
