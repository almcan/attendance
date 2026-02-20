#!/usr/bin/env python3
"""
FeliCa 出席確認システム
========================
FeliCa カードリーダー（PaSoRi 等）を使って学生の出席を記録するプログラム。

使い方:
  .venv/bin/python main.py              → 出席確認モード
  .venv/bin/python main.py --register   → カード登録モード
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


def get_today_attendance_file() -> Path:
    """本日の出席記録ファイルのパスを返す。"""
    today = datetime.now().strftime("%Y-%m-%d")
    return ATTENDANCE_DIR / f"{today}.csv"


def load_today_attendance() -> dict:
    """
    本日の出席記録を読み込み、IDm → 最新ステータス の辞書を返す。
    ステータス: "出席" または "退席"
    """
    attendance_file = get_today_attendance_file()
    status = {}
    if attendance_file.exists():
        with open(attendance_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                idm = row["idm"].strip().upper()
                status[idm] = row.get("status", "出席").strip()
    return status


def record_attendance(idm: str, student_id: str, name: str, status: str):
    """出席または退席を CSV ファイルに記録する。"""
    attendance_file = get_today_attendance_file()
    file_exists = attendance_file.exists()
    with open(attendance_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["idm", "student_id", "name", "status", "timestamp"])
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([idm, student_id, name, status, now])


def register_student(idm: str, student_id: str, name: str):
    """学生を students.csv に追記する。"""
    with open(STUDENTS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([idm, student_id, name])


# ─── 表示ヘルパー ───────────────────────────────────────────

RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"


def print_header(mode: str):
    """ヘッダーを表示する。"""
    os.system("clear" if os.name != "nt" else "cls")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  FeliCa 出席確認システム  —  {mode}{RESET}")
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
    """出席確認モード: カードタッチで出席を記録。"""
    students = load_students()
    status_map = load_today_attendance()
    terminate_flag = False

    def handle_sigint(signum, frame):
        nonlocal terminate_flag
        terminate_flag = True

    signal.signal(signal.SIGINT, handle_sigint)

    if not students:
        print(f"{RED}[警告] 学生名簿が空です。先に --register で学生を登録してください。{RESET}")
        print()

    print_header("出席確認モード")
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
                record_attendance(idm, student["student_id"], student["name"], "出席")
                status_map[idm] = "出席"
                print(f"  {GREEN}✅ [{now_str}] {student['name']} ({student['student_id']}) — 出席{RESET}")
            elif current_status == "出席":
                # 2回目タッチ → 退席
                record_attendance(idm, student["student_id"], student["name"], "退席")
                status_map[idm] = "退席"
                print(f"  {YELLOW}🚪 [{now_str}] {student['name']} ({student['student_id']}) — 退席{RESET}")
            else:
                # 退席後にタッチ → 出席
                record_attendance(idm, student["student_id"], student["name"], "出席")
                status_map[idm] = "出席"
                print(f"  {GREEN}✅ [{now_str}] {student['name']} ({student['student_id']}) — 出席{RESET}")

            # サマリー更新
            total = len(students)
            present = sum(1 for i in students if status_map.get(i) == "出席")
            print(f"       📊 在席: {present} / {total} 人")
        else:
            # 未登録カード
            print(f"  {RED}❌ [{now_str}] 未登録のカードです (IDm: {idm}){RESET}")
            print(f"       カードを登録するには --register モードを使用してください")

        return True  # True を返すとタグが離れるまで待つ

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


# ─── カード登録モード ───────────────────────────────────────

def register_mode():
    """カード登録モード: カードの IDm を読み取り、学生情報を入力して名簿に追記。"""
    students = load_students()
    terminate_flag = False

    def handle_sigint(signum, frame):
        nonlocal terminate_flag
        terminate_flag = True

    signal.signal(signal.SIGINT, handle_sigint)

    print_header("カード登録モード")
    print(f"  登録済み学生数: {BOLD}{len(students)}{RESET} 人")
    print()
    print(f"  {YELLOW}▶ 登録するカードをリーダーにタッチしてください...{RESET}")
    print(f"  {YELLOW}  (終了: Ctrl+C){RESET}")
    print()

    def on_connect(tag):
        """カード接続時のコールバック。"""
        nonlocal students

        idm = tag.identifier.hex().upper()

        if idm in students:
            student = students[idm]
            print(f"  {YELLOW}⚠ このカードは既に登録されています:{RESET}")
            print(f"    IDm: {idm}")
            print(f"    学籍番号: {student['student_id']}")
            print(f"    氏名: {student['name']}")
            print()
            return True

        print(f"  {CYAN}📇 新しいカードを検出しました{RESET}")
        print(f"    IDm: {BOLD}{idm}{RESET}")
        print()

        # 学生情報の入力
        try:
            student_id = input(f"    学籍番号を入力: ").strip()
            if not student_id:
                print(f"  {RED}  登録をキャンセルしました{RESET}")
                print()
                return True

            name = input(f"    氏名を入力: ").strip()
            if not name:
                print(f"  {RED}  登録をキャンセルしました{RESET}")
                print()
                return True

            register_student(idm, student_id, name)
            students[idm] = {"student_id": student_id, "name": name}

            print()
            print(f"  {GREEN}✅ 登録完了!{RESET}")
            print(f"    学籍番号: {student_id}")
            print(f"    氏名: {name}")
            print(f"    IDm: {idm}")
            print(f"    登録済み学生数: {len(students)} 人")
            print()
            print(f"  {YELLOW}▶ 次のカードをタッチしてください...{RESET}")
            print()

        except EOFError:
            pass

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
        sys.exit(1)

    print(f"  {GREEN}[OK] カードリーダーに接続しました{RESET}")
    print()

    while not terminate_flag:
        clf.connect(rdwr={"on-connect": on_connect},
                    terminate=lambda: terminate_flag)

    clf.close()
    print(f"\n{CYAN}[INFO] 登録を終了します。{RESET}")
    print(f"  登録済み学生数: {len(students)} 人")


# ─── メイン ─────────────────────────────────────────────────

def main():
    ensure_dirs()

    if "--register" in sys.argv or "-r" in sys.argv:
        register_mode()
    else:
        attendance_mode()


if __name__ == "__main__":
    main()
