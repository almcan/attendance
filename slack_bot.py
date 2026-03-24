#!/usr/bin/env python3
"""
Slack Bot — DM で出席/退席を変更するモジュール
================================================
学生が Slack Bot に DM を送ることで、カードタッチなしに
出席ステータス（出席 ↔ 退席）を切り替えられます。

Socket Mode を使用するため、サーバーの公開設定は不要です。

必要な環境変数 (.env):
  SLACK_BOT_TOKEN  : xoxb-... (Bot Token)
  SLACK_APP_TOKEN  : xapp-... (App-Level Token, Socket Mode 用)

使い方 (単体起動):
  .venv/bin/python slack_bot.py
"""

import csv
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

try:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
except ImportError:
    print("エラー: slack-bolt がインストールされていません。")
    print("  pip install slack-bolt")
    sys.exit(1)

# ─── 設定 ────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
STUDENTS_CSV = BASE_DIR / "students.csv"
ATTENDANCE_DIR = BASE_DIR / "attendance"

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")

if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
    print("[Error] SLACK_BOT_TOKEN または SLACK_APP_TOKEN が設定されていません。")
    print("  .env ファイルを確認してください。")
    sys.exit(1)

import logging
logging.basicConfig(level=logging.INFO)

app = App(token=SLACK_BOT_TOKEN)

# ─── ユーティリティ ──────────────────────────────────────────

def find_student_by_slack_id(slack_user_id: str) -> dict | None:
    """
    students.csv から slack_id でユーザーを検索する。
    見つかった場合は {"name": "...", "idm": "..."} を返す。
    """
    if not STUDENTS_CSV.exists():
        return None
    with open(STUDENTS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("slack_id", "").strip() == slack_user_id:
                return {"name": row["name"].strip(), "idm": row.get("idm", "").strip()}
    return None


def get_current_status(name: str) -> str | None:
    """
    学生の当日の最新ステータスを返す。
    記録がない場合は None を返す。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    attendance_file = ATTENDANCE_DIR / f"{name}.csv"
    if not attendance_file.exists():
        return None

    latest = None
    with open(attendance_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("date", "").strip() == today:
                latest = row.get("status", "").strip()
    return latest  # 当日の記録がなければ None


def do_record(name: str, status: str):
    """attendance.py の record_attendance を呼び出す。"""
    from attendance import record_attendance
    record_attendance(name, status)


# ─── メッセージハンドラ ───────────────────────────────────────

HELP_TEXT = (
    "*コマンド一覧*\n"
    "• `退席` / `leave`  → 退席を記録\n"
    "• `状態` / `status` → 現在のステータスを確認\n"
    "• `ヘルプ` / `help` → このメッセージを表示\n"
    "\n"
    "※ 出席はカードタッチで記録してください。"
)

STATUS_EMOJI = {"出席": "🟢", "退席": "🔴"}


# ─── デバッグ: すべてのイベントをログ出力 ───────────────────
@app.middleware
def log_event(event, logger, next):
    logger.info(f"Received event: {event.get('type')} - {event}")
    return next()


@app.event("message")
def handle_dm(event, say, logger):
    """DM チャンネルのメッセージに応答する（退席のみ）。"""
    # DM 以外（チャンネル投稿など）は無視
    if event.get("channel_type") != "im":
        return

    # Bot 自身のメッセージは無視
    if event.get("bot_id"):
        return

    user_id = event.get("user")
    text = (event.get("text") or "").strip()
    cmd = text.lower()

    # 学生を検索
    student = find_student_by_slack_id(user_id)
    if student is None:
        say(
            "❌ あなたの Slack ID は出席システムに登録されていません。\n"
            "管理者に Slack ID の登録を依頼してください。"
        )
        return

    name = student["name"]
    current = get_current_status(name)
    current_label = current if current else "未記録"
    emoji = STATUS_EMOJI.get(current_label, "⚪")

    # ── ステータス確認 ──────────────────────────────────────
    if cmd in ("状態", "status"):
        say(f"{emoji} *{name}* さんの現在のステータス: *{current_label}*")

    # ── 退席 ────────────────────────────────────────────────
    elif cmd in ("退席", "leave"):
        if current == "退席":
            say(f"*{name}* さんはすでに *退席済み* です。")
        elif current is None:
            say(
                f"*{name}* さんはまだ出席記録がありません。\n"
                "先にカードをタッチして出席を記録してください。"
            )
        else:
            do_record(name, "退席")
            say(f"*{name}* さんの *退席* を記録しました！")

    # ── ヘルプ ──────────────────────────────────────────────
    elif cmd in ("ヘルプ", "help"):
        say(HELP_TEXT)

    # ── 不明コマンド ────────────────────────────────────────
    else:
        say(f"コマンドが認識できませんでした。\n\n{HELP_TEXT}")


# ─── 起動 ───────────────────────────────────────────────────

def run_slack_bot():
    """Socket Mode ハンドラを起動する（スレッド内から呼び出し可）。"""
    try:
        auth_test = app.client.auth_test()
        bot_user_id = auth_test["user_id"]
        bot_name = auth_test["user"]
        print(f"\033[94m  [OK] Slack Bot 起動: {bot_name} ({bot_user_id})\033[0m")
    except Exception as e:
        print(f"\033[91m  [Error] Slack Auth Test 失敗: {e}\033[0m")

    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    run_slack_bot()
