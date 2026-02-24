#!/usr/bin/env python3
"""
FeliCa 出席確認システム — カード登録モード
==========================================
FeliCa カードリーダー（PaSoRi 等）を使って学生のカードIDmを読み取り、名簿に登録する。

使い方:
  .venv/bin/python register.py
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

# ─── ユーティリティ関数 ─────────────────────────────────────

def ensure_files():
    """必要なファイルを作成する。"""
    if not STUDENTS_CSV.exists():
        with open(STUDENTS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["idm", "name"])
        print(f"[INFO] 学生名簿ファイルを作成しました: {STUDENTS_CSV}")


def load_students() -> dict:
    """
    students.csv を読み込み、IDm → {name} の辞書を返す。
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
                "name": row["name"].strip(),
            }
    return students


def register_student(idm: str, name: str):
    """学生を students.csv に追記する。"""
    with open(STUDENTS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([idm, name])


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
    print(f"{BOLD}{CYAN}  FeliCa 出席確認システム — カード登録{RESET}")
    print(f"{BOLD}{CYAN}  {datetime.now().strftime('%Y年%m月%d日 %H:%M')}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")
    print()


# ─── カード登録モード ───────────────────────────────────────

def register_mode():
    """カード登録モード: カードの IDm を読み取り、学生情報を入力して名簿に追記。"""
    ensure_files()
    students = load_students()
    terminate_flag = False

    def handle_sigint(signum, frame):
        nonlocal terminate_flag
        terminate_flag = True

    signal.signal(signal.SIGINT, handle_sigint)

    print_header()
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
            print(f"    氏名: {student['name']}")
            print()
            return True

        print(f"  {CYAN}📇 新しいカードを検出しました{RESET}")
        print(f"    IDm: {BOLD}{idm}{RESET}")
        print()

        # 学生情報の入力
        try:
            name = input(f"    氏名を入力: ").strip()
            if not name:
                print(f"  {RED}  登録をキャンセルしました{RESET}")
                print()
                return True

            register_student(idm, name)
            students[idm] = {"name": name}

            print()
            print(f"  {GREEN}✅ 登録完了!{RESET}")
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


if __name__ == "__main__":
    register_mode()
