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
                "student_id": row["student_id"].strip(),
                "name": row["name"].strip(),
            })
    return students


def get_student_status(student_id: str, name: str) -> dict:
    """学生個人のCSVから本日の最新ステータスを取得する。"""
    today = datetime.now().strftime("%Y-%m-%d")
    filepath = ATTENDANCE_DIR / f"{student_id}_{name}.csv"
    status = None
    timestamp = None

    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["date"] == today:
                    status = row.get("status", "出席").strip()
                    timestamp = row.get("timestamp", "").strip()

    return {"status": status, "timestamp": timestamp}


def match_student_to_seat(seat_name: str, students: list) -> dict | None:
    """座席名から students.csv の学生を検索する。"""
    for s in students:
        # 名前の最初の部分（姓）で照合
        # 例: 座席 "横平" → students.csv "横平徳美"
        clean_seat = seat_name.replace("(", "").replace(")", "").replace("（", "").replace("）", "")
        if s["name"].startswith(clean_seat) or clean_seat in s["name"]:
            return 
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
                    info = get_student_status(student["student_id"], student["name"])
                    seat_data = {
                        "seat_name": seat_name,
                        "full_name": student["name"],
                        "student_id": student["student_id"],
                        "status": info["status"],
                        "timestamp": info["timestamp"],
                    }
                    if info["status"] == "出席":
                        present_count += 1
                else:
                    seat_data = {
                        "seat_name": seat_name,
                        "full_name": None,
                        "student_id": None,
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
    student_id = data.get("student_id", "").strip()
    name = data.get("name", "").strip()

    if not student_id or not name:
        return jsonify({"error": "学籍番号と氏名は必須です"}), 400

    # students.csv に追記
    if not STUDENTS_CSV.exists():
        with open(STUDENTS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["idm", "student_id", "name"])

    with open(STUDENTS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["", student_id, name])

    # 全クライアントに通知
    notify_clients()

    return jsonify({"ok": True})


if __name__ == "__main__":
    print("=" * 50)
    print("  出席ダッシュボード起動中...")
    print("  http://localhost:5000 でアクセス")
    print("  終了: Ctrl+C")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False)
