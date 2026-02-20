#!/usr/bin/env python3
"""
FeliCa 出席確認システム — 出席モード
====================================
FeliCa カードリーダー（PaSoRi 等）を使って学生の出席を記録するプログラム。

使い方:
  .venv/bin/python attendance.py
"""

import csv
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

try:
    import nfc
except ImportError:
    print("=" * 60)
    print("エラー: nfcpy がインストールされていません。")
    print("以下のコマンドでインストールしてください:")
    print("  pip install nfcpy")
    print("=" * 60)
    sys.exit(1)

# ─── 定数 ───────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
STUDENTS_CSV = BASE_DIR / "students.csv"
ATTENDANCE_DIR = BASE_DIR / "attendance"

# ─── ユーティリティ関数 ─────────────────────────────────────

def ensure_dirs():
    """必要なディレクトリとファイルを作成する。"""
    ATTENDANCE_DIR.mkdir(exist_ok=True)
    if not STUDENTS_CSV.exists():
        with open(STUDENTS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["idm", "student_id", "name"])
        print(f"[INFO] 学生名簿ファイルを作成しました: {STUDENTS_CSV}")


def load_students() -> dict:
    """
    students.csv を読み込み、IDm → {student_id, name} の辞書を返す。
    IDm は大文字に正規化される。
    """
    students = {}
    if not STUDENTS_CSV.exists():
        return students
    with open(STUDENTS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            idm = row["idm"].strip().upper()
            students[idm] = {
                "student_id": row["student_id"].strip(),
                "name": row["name"].strip(),
            }
    return students


def get_student_attendance_file(student_id: str, name: str) -> Path:
    """学生個人の出席記録ファイルのパスを返す。"""
    return ATTENDANCE_DIR / f"{student_id}_{name}.csv"


def load_today_attendance(students: dict) -> dict:
    """
    全学生のCSVから本日の最新ステータスを読み込み、IDm -> ステータス の辞書を返す。
    ステータス: "出席" または "退席"
    """
    today = datetime.now().strftime("%Y-%m-%d")
    status = {}
    for idm, info in students.items():
        filepath = get_student_attendance_file(info["student_id"], info["name"])
        if not filepath.exists():
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["date"] == today:
                    status[idm] = row.get("status", "出席").strip()
    return status


def record_attendance(student_id: str, name: str, status: str):
    """出席または退席を学生個人の CSV ファイルに記録する。"""
    attendance_file = get_student_attendance_file(student_id, name)
    file_exists = attendance_file.exists()
    with open(attendance_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "status", "timestamp"])
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        date = datetime.now().strftime("%Y-%m-%d")
        writer.writerow([date, status, now])

    # ダッシュボードにリアルタイム通知
    try:
        from dashboard import notify_clients
        notify_clients()
    except Exception:
        pass  # ダッシュボード未起動時は無視


# ─── 表示ヘルパー ───────────────────────────────────────────

RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"


def print_header():
    """ヘッダーを表示する。"""
    os.system("clear" if os.name != "nt" else "cls")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  FeliCa 出席確認システム{RESET}")
    print(f"{BOLD}{CYAN}  {datetime.now().strftime('%Y年%m月%d日 %H:%M')}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")
    print()


def print_attendance_summary(students: dict, status_map: dict):
    """現在の出席状況サマリーを表示する。"""
    total = len(students)
    present = sum(1 for idm in students if status_map.get(idm) == "出席")
    print(f"  📊 在席状況: {BOLD}{GREEN}{present}{RESET} / {total} 人")
    print(f"  {'─' * 40}")


# ─── 出席確認モード ─────────────────────────────────────────

def attendance_mode():
    """出席確認モード: カードタッチで出席/退席を記録。"""
    ensure_dirs()
    students = load_students()
    status_map = load_today_attendance(students)
    terminate_flag = False

    def handle_sigint(signum, frame):
        nonlocal terminate_flag
        terminate_flag = True

    signal.signal(signal.SIGINT, handle_sigint)

    if not students:
        print(f"{RED}[警告] 学生名簿が空です。先に register.py で学生を登録してください。{RESET}")
        print()

    print_header()
    print_attendance_summary(students, status_map)
    print()
    print(f"  {YELLOW}▶ カードをリーダーにタッチしてください...{RESET}")
    print(f"  {YELLOW}  （1回目: 出席 ／ 2回目: 退席）{RESET}")
    print(f"  {YELLOW}  (終了: Ctrl+C){RESET}")
    print()

    def on_connect(tag):
        """カード接続時のコールバック。"""
        nonlocal students, status_map

        idm = tag.identifier.hex().upper()
        now_str = datetime.now().strftime("%H:%M:%S")

        if idm in students:
            student = students[idm]
            current_status = status_map.get(idm)

            if current_status is None:
                # 初回タッチ → 出席
                record_attendance(student["student_id"], student["name"], "出席")
                status_map[idm] = "出席"
                print(f"  {GREEN}✅ [{now_str}] {student['name']} ({student['student_id']}) — 出席{RESET}")
            elif current_status == "出席":
                # 2回目タッチ → 退席
                record_attendance(student["student_id"], student["name"], "退席")
                status_map[idm] = "退席"
                print(f"  {YELLOW}🚪 [{now_str}] {student['name']} ({student['student_id']}) — 退席{RESET}")
            else:
                # 退席後にタッチ → 出席
                record_attendance(student["student_id"], student["name"], "出席")
                status_map[idm] = "出席"
                print(f"  {GREEN}✅ [{now_str}] {student['name']} ({student['student_id']}) — 出席{RESET}")

            # サマリー更新
            total = len(students)
            present = sum(1 for i in students if status_map.get(i) == "出席")
            print(f"       📊 在席: {present} / {total} 人")
        else:
            # 未登録カード
            print(f"  {RED}❌ [{now_str}] 未登録のカードです (IDm: {idm}){RESET}")
            print(f"       カードを登録するには register.py を使用してください")

        return True

    # NFC リーダーに接続
    try:
        clf = nfc.ContactlessFrontend("usb")
    except Exception as e:
        print(f"\n{RED}[エラー] カードリーダーに接続できませんでした。{RESET}")
        print(f"  詳細: {e}")
        print(f"\n  対処法:")
        print(f"  1. カードリーダーが USB に接続されているか確認")
        print(f"  2. Linux の場合: sudo 権限が必要な場合があります")
        print(f"     udev ルールの設定を確認してください")
        sys.exit(1)

    print(f"  {GREEN}[OK] カードリーダーに接続しました{RESET}")
    print()

    while not terminate_flag:
        clf.connect(rdwr={"on-connect": on_connect},
                    terminate=lambda: terminate_flag)

    clf.close()
    print(f"\n{CYAN}[INFO] プログラムを終了します。お疲れさまでした！{RESET}")


if __name__ == "__main__":
    attendance_mode()
