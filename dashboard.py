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
import hashlib
import io
import json
import os
import queue
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import jpholiday
from flask import (
    Flask, Response, jsonify, redirect, render_template,
    request, send_file, session, url_for
)
from functools import wraps

# ─── localhost 制限 + 管理者認証 ──────────────────────────────

ADMIN_USERNAME_HASH = os.environ.get("ADMIN_USERNAME_HASH", "")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_localhost() -> bool:
    """リクエストが localhost からのものか確認。"""
    try:
        return request.remote_addr in ("127.0.0.1", "::1")
    except RuntimeError:
        return False


def _is_admin_session() -> bool:
    """セッションの認証状況を確認。"""
    try:
        return session.get("admin_authenticated") is True
    except RuntimeError:
        return False


def admin_required(f):
    """ローカルホストから、またはログイン済みセッションからのリクエストのみ許可するデコレーター。"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if _is_localhost() or _is_admin_session():
            return f(*args, **kwargs)
        return redirect(url_for("admin_login", next=request.url))
    return decorated


def localhost_only(f):
    """localhost (127.0.0.1 / ::1) からのリクエストのみ許可するデコレーター。"""
    @wraps(f)
    def decorated(*args, **kwargs):
        remote = request.remote_addr
        if remote not in ("127.0.0.1", "::1"):
            return jsonify({"error": "localhost のみ利用可能です"}), 403
        return f(*args, **kwargs)
    return decorated


# ─── 定数 ───────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
STUDENTS_CSV = BASE_DIR / "students.csv"
HOLIDAYS_CSV = BASE_DIR / "holidays.csv"
ATTENDANCE_DIR = BASE_DIR / "attendance"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24)

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
    time_val = None
    date_val = None
    reason = ""

    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                status = row.get("status", "出席").strip()
                timestamp = row.get("timestamp", "").strip()
                date_str = row.get("date", "").strip()
                reason = row.get("reason", "").strip()
                if date_str:
                    try:
                        # 2026-03-16 -> 03/16
                        dt = datetime.strptime(date_str, "%Y-%m-%d")
                        date_val = dt.strftime("%m/%d")
                    except:
                        date_val = date_str
                
                if timestamp and " " in timestamp:
                    time_val = timestamp.split(" ")[1]
                else:
                    time_val = timestamp

    # 12時間経過していたら自動的に「退席」扱いにする
    if status in ("出席", "リモート中") and timestamp:
        try:
            last_dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            if datetime.now() - last_dt > timedelta(hours=12):
                status = "退席"
        except:
            pass

    return {"status": status, "date": date_val, "time": time_val, "reason": reason}


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
                        "status": info["status"],
                        "date": info["date"],
                        "time": info["time"],
                    }
                    if info["status"] in ("出席", "リモート中"):
                        present_count += 1
                else:
                    seat_data = {
                        "seat_name": seat_name,
                        "full_name": None,
                        "status": None,
                        "date": None,
                        "time": None,
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


@app.route("/calendar")
def calendar_page():
    """カレンダーページ。"""
    return render_template("calendar.html")


@app.route("/admin")
@admin_required
def admin_page():
    """管理画面（休日設定など）。localhost または認証済みのみ。"""
    # 学生一覧を渡す
    students = []
    if STUDENTS_CSV.exists():
        with open(STUDENTS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                students.append(row["name"].strip())
    return render_template("admin.html", students=students)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """管理者ログインページ。localhostからはスキップ。"""
    if _is_localhost() or _is_admin_session():
        return redirect(url_for("admin_page"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not ADMIN_USERNAME_HASH or not ADMIN_PASSWORD_HASH:
            error = "管理者認証情報が未設定です。サーバー管理者に連絡してください。"
        elif (_sha256(username) == ADMIN_USERNAME_HASH and
              _sha256(password) == ADMIN_PASSWORD_HASH):
            session["admin_authenticated"] = True
            session.permanent = False
            next_url = request.args.get("next") or url_for("admin_page")
            return redirect(next_url)
        else:
            error = "ユーザー名またはパスワードが違います。"

    return render_template("admin_login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    """セッションを削除してログアウト。"""
    session.pop("admin_authenticated", None)
    return redirect(url_for("admin_login"))


@app.route("/api/status")
def api_status():
    """全学生の在席状況を JSON で返す。"""
    return jsonify(_build_status_data())


@app.route("/api/calendar")
def api_calendar():
    """月別の出席データを返す。?year=YYYY&month=MM"""
    from calendar import monthrange
    now = datetime.now()
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))

    # 休日情報取得 (jpholiday + custom)
    holidays = {}  # date_str -> name
    # 日本の祝日
    for date, name in jpholiday.month_holidays(year, month):
        holidays[date.strftime("%Y-%m-%d")] = name
    # カスタム休日 (夏休み等)
    if HOLIDAYS_CSV.exists():
        with open(HOLIDAYS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = row["date"].strip()
                if d.startswith(f"{year}-{month:02d}"):
                    holidays[d] = row["name"].strip()

    # 学生一覧取得
    students = []
    if STUDENTS_CSV.exists():
        with open(STUDENTS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                students.append(row["name"].strip())

    # 月内の全日付を用意
    _, days_in_month = monthrange(year, month)
    # date_str -> {name: status}
    daily: dict[str, dict[str, str]] = {}
    for d in range(1, days_in_month + 1):
        date_str = f"{year}-{month:02d}-{d:02d}"
        daily[date_str] = {}

    # 各学生のCSVを読んで当月分を収集
    for name in students:
        filepath = ATTENDANCE_DIR / f"{name}.csv"
        if not filepath.exists():
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            latest: dict[str, str] = {}  # date_str -> 最終ステータス
            for row in reader:
                d = row.get("date", "").strip()
                s = row.get("status", "").strip()
                if d.startswith(f"{year}-{month:02d}"):
                    latest[d] = s  # 最後の行で上書き → 当日最終ステータス
            for d, s in latest.items():
                if d in daily:
                    daily[d][name] = s

    return jsonify({
        "year": year,
        "month": month,
        "students": students,
        "daily": daily,
        "holidays": holidays,
    })


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
@localhost_only
def api_register():
    """ブラウザから学生を登録する。"""
    data = request.get_json()
    name = data.get("name", "").strip()
    idm = data.get("idm", "").strip()

    if not name:
        return jsonify({"error": "氏名は必須です"}), 400

    # students.csv に追記
    fieldnames = ["idm", "name"]
    file_exists = STUDENTS_CSV.exists()
    
    if file_exists:
        with open(STUDENTS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                fieldnames = reader.fieldnames

    with open(STUDENTS_CSV, "a" if file_exists else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        
        # 新しい行を作成（既存のカラムに対応）
        new_row = {fn: "" for fn in fieldnames}
        new_row["idm"] = idm
        new_row["name"] = name
        writer.writerow(new_row)

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
@localhost_only
def api_pause():
    """出席登録を一時停止する（モーダル表示中）。"""
    pending_reassign["paused"] = True
    return jsonify({"ok": True})


@app.route("/api/pause", methods=["DELETE"])
@localhost_only
def api_resume():
    """出席登録を再開する（モーダル閉じた時）。"""
    pending_reassign["paused"] = False
    return jsonify({"ok": True})


@app.route("/api/capture", methods=["POST"])
@localhost_only
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
@localhost_only
def api_capture_cancel():
    """IDmキャプチャをキャンセルする。"""
    pending_capture["active"] = False
    pending_capture["idm"] = None
    return jsonify({"ok": True})


@app.route("/api/reassign", methods=["POST"])
@localhost_only
def api_reassign():
    """IDm 再登録を開始する。次のカードタッチで IDm を更新。"""
    data = request.get_json()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "氏名は必須です"}), 400
    pending_reassign["name"] = name
    return jsonify({"ok": True, "message": f"カードをタッチしてください"})


@app.route("/api/reassign", methods=["DELETE"])
@localhost_only
def api_cancel_reassign():
    """IDm 再登録をキャンセルする。"""
    pending_reassign["name"] = None
    return jsonify({"ok": True})


# ─── 休日管理 API ──────────────────────────────────────────
@app.route("/api/holidays", methods=["GET"])
def api_get_holidays():
    """登録されているカスタム休日一覧を返す。"""
    hList = []
    if HOLIDAYS_CSV.exists():
        with open(HOLIDAYS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                hList.append(row)
    # 日付順にソート
    hList.sort(key=lambda x: x["date"])
    return jsonify(hList)


@app.route("/api/holidays", methods=["POST"])
@localhost_only
def api_add_holiday():
    """休日を追加する（単一または範囲）。"""
    data = request.get_json()
    start_str = data.get("date")  # 開始日
    end_str = data.get("end_date") # 終了日 (任意)
    name = data.get("name")
    
    if not start_str or not name:
        return jsonify({"error": "日付と名称が必要です"}), 400

    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        dates_to_add = [start_str]
        
        if end_str:
            end_dt = datetime.strptime(end_str, "%Y-%m-%d")
            if end_dt < start_dt:
                return jsonify({"error": "終了日は開始日より後である必要があります"}), 400
            
            # 範囲内の全日付をリストアップ
            current = start_dt + timedelta(days=1)
            while current <= end_dt:
                dates_to_add.append(current.strftime("%Y-%m-%d"))
                current += timedelta(days=1)
    except ValueError:
        return jsonify({"error": "日付形式が正しくありません(YYYY-MM-DD)"}), 400

    # 既存の重複をチェックするか、単純に追記するか
    # 今回は単純に追記（カレンダー表示側で辞書化されるため実害は少ないが、きれいに保つなら要検討）
    file_exists = HOLIDAYS_CSV.exists()
    
    # 既存の日付セットを取得（重複回避）
    existing_dates = set()
    if file_exists:
        with open(HOLIDAYS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_dates.add(row["date"])

    with open(HOLIDAYS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "name"])
        if not file_exists:
            writer.writeheader()
        
        added_count = 0
        for d in dates_to_add:
            if d not in existing_dates:
                writer.writerow({"date": d, "name": name})
                added_count += 1

    return jsonify({"ok": True, "added": added_count})


@app.route("/api/holidays/<date_str>", methods=["DELETE"])
@localhost_only
def api_delete_holiday(date_str):
    """指定した日付の休日を削除する。"""
    if not HOLIDAYS_CSV.exists():
        return jsonify({"ok": True})

    rows = []
    with open(HOLIDAYS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["date"] != date_str:
                rows.append(row)

    with open(HOLIDAYS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "name"])
        writer.writeheader()
        writer.writerows(rows)

    return jsonify({"ok": True})


# ─── CSV / ZIP ダウンロード API ─────────────────────────────────

def _calc_daily_hours(name: str) -> list[dict]:
    """学生個人のCSVから日別在籍時間（リモート除く）を計算して返す。

    Returns:
        [{'date': 'YYYY-MM-DD', 'hours': 1.23}, ...] 日付昇順
    """
    filepath = ATTENDANCE_DIR / f"{name}.csv"
    if not filepath.exists():
        return []

    # 全行を読み込む
    rows = []
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = row.get("date", "").strip()
            status   = row.get("status", "").strip()
            ts_str   = row.get("timestamp", "").strip()
            if not date_str or not status or not ts_str:
                continue
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            rows.append({"date": date_str, "status": status, "ts": ts})

    # 日付ごとに在籍秒数を集計
    # 「出席」→「退席」のペアのみカウント（リモート中は除外）
    # 日をまたぐペアは出席日の23:59:59で打ち切る
    daily_seconds: dict[str, float] = {}
    last_attend: datetime | None = None   # 直近「出席」タイムスタンプ
    last_attend_date: str | None = None   # 直近「出席」の日付文字列

    def _add_secs(date_key: str, secs: float) -> None:
        if date_key not in daily_seconds:
            daily_seconds[date_key] = 0.0
        daily_seconds[date_key] += secs

    for r in rows:
        date_str = r["date"]
        # 日付エントリを確保
        if date_str not in daily_seconds:
            daily_seconds[date_str] = 0.0

        if r["status"] == "出席":
            last_attend = r["ts"]
            last_attend_date = date_str

        elif r["status"] in ("退席", "リモート中"):
            if last_attend is not None and last_attend_date is not None:
                if r["ts"].date() != last_attend.date():
                    # 日をまたぐ場合は出席日の 23:59:59 で打ち切り
                    midnight = datetime(
                        last_attend.year, last_attend.month, last_attend.day,
                        23, 59, 59
                    )
                    diff = max((midnight - last_attend).total_seconds(), 0.0)
                    _add_secs(last_attend_date, diff)
                else:
                    diff = (r["ts"] - last_attend).total_seconds()
                    if diff > 0:
                        _add_secs(last_attend_date, diff)
            last_attend = None
            last_attend_date = None

    # 最後に退席していない場合（現在も在席中）は現時刻まで加算（最大12時間）
    if last_attend is not None and last_attend_date is not None:
        now = datetime.now()
        if now.date() != last_attend.date():
            # 今日でなければ出席日の 23:59:59 で打ち切り
            midnight = datetime(
                last_attend.year, last_attend.month, last_attend.day,
                23, 59, 59
            )
            diff = max((midnight - last_attend).total_seconds(), 0.0)
        else:
            diff = min((now - last_attend).total_seconds(), 12 * 3600)
        if diff > 0:
            _add_secs(last_attend_date, diff)

    result = [
        {"date": d, "hours": round(s / 3600, 2)}
        for d, s in sorted(daily_seconds.items())
    ]
    return result


def _make_summary_csv_bytes(name: str) -> bytes:
    """日別在籍時間集計を CSV バイト列で返す。"""
    records = _calc_daily_hours(name)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["date", "hours"])
    writer.writeheader()
    writer.writerows(records)
    return buf.getvalue().encode("utf-8-sig")  # BOM付き UTF-8（Excel対応）


@app.route("/api/admin/download/attendance/<name>")
@admin_required
def download_attendance_csv(name: str):
    """学生個別の日別在籍時間集計CSVをダウンロードする（リモート除く）。"""
    filepath = ATTENDANCE_DIR / f"{name}.csv"
    if not filepath.exists():
        return jsonify({"error": f"{name} の出席データが見つかりません"}), 404
    csv_bytes = _make_summary_csv_bytes(name)
    buf = io.BytesIO(csv_bytes)
    return send_file(
        buf,
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=f"{name}_在籍時間.csv",
    )


@app.route("/api/admin/download/attendance_zip")
@admin_required
def download_attendance_zip():
    """全学生の日別在籍時間集計CSVを ZIP でまとめてダウンロード。"""
    students = []
    if STUDENTS_CSV.exists():
        with open(STUDENTS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                students.append(row["name"].strip())

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(students):
            csv_bytes = _make_summary_csv_bytes(name)
            zf.writestr(f"{name}_在籍時間.csv", csv_bytes)
    buf.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"在籍時間_{timestamp}.zip",
    )


@app.route("/api/admin/download/absent_csv")
@admin_required
def download_absent_csv():
    """全学生の欠席記録を CSV で直接ダウンロード（欠席ステータスのみ）。"""
    students = []
    if STUDENTS_CSV.exists():
        with open(STUDENTS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                students.append(row["name"].strip())

    # 全学生の欠席レコードを収集
    records: list[dict] = []
    for name in sorted(students):
        filepath = ATTENDANCE_DIR / f"{name}.csv"
        if not filepath.exists():
            continue
        seen_dates: set[str] = set()
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_str = row.get("date", "").strip()
                status   = row.get("status", "").strip()
                reason   = row.get("reason", "").strip()
                if status == "欠席" and date_str and date_str not in seen_dates:
                    records.append({"氏名": name, "日付": date_str, "理由": reason})
                    seen_dates.add(date_str)

    records.sort(key=lambda x: (x["日付"], x["氏名"]))

    buf_str = io.StringIO()
    writer = csv.DictWriter(buf_str, fieldnames=["氏名", "日付", "理由"])
    writer.writeheader()
    writer.writerows(records)
    csv_bytes = buf_str.getvalue().encode("utf-8-sig")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        io.BytesIO(csv_bytes),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=f"欠席記録_{timestamp}.csv",
    )


if __name__ == "__main__":
    print("=" * 50)
    print("  出席ダッシュボード起動中...")
    print("  http://localhost:5000 でアクセス")
    print("  終了: Ctrl+C")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False)
