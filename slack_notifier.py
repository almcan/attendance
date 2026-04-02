#!/usr/bin/env python3
import csv
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
import jpholiday
from dotenv import load_dotenv

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:
    print("エラー: slack-sdk がインストールされていません。")
    print("以下のコマンドでインストールしてください:")
    print("  pip install slack-sdk python-dotenv")
    sys.exit(1)

# .env ファイルの読み込み
load_dotenv()

# ─── 設定 ───────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
STUDENTS_CSV = BASE_DIR / "students.csv"
HOLIDAYS_CSV = BASE_DIR / "holidays.csv"
ATTENDANCE_DIR = BASE_DIR / "attendance"

# Slack 設定
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
# デフォルトの通知先チャンネル（Webhookを使わない場合）
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#attendance-notices")

client = WebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None

def get_last_5_business_days():
    """直近5営業日の日付リストを返す。"""
    days = []
    current = datetime.now()
    # 17時前なら昨日の分からカウント
    if current.hour < 17:
        current -= timedelta(days=1)
        
    while len(days) < 5:
        # 0:月 ~ 4:金
        if not is_holiday(current):
            days.append(current.strftime("%Y-%m-%d"))
        current -= timedelta(days=1)
    return days

def is_holiday(dt):
    """該当日が週末・祝日・カスタム休日（夏休み等）かどうかを判定。"""
    # 1. 週末チェック
    if dt.weekday() >= 5:
        return True
    
    # 2. 祝日チェック (jpholiday)
    if jpholiday.is_holiday(dt):
        return True
    
    # 3. カスタム休日チェック (holidays.csv)
    date_str = dt.strftime("%Y-%m-%d")
    if HOLIDAYS_CSV.exists():
        with open(HOLIDAYS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("date") == date_str:
                    return True
                    
    return False

def check_attendance():
    """1週間（5営業日）出席していない人を特定して通知する。"""
    # 今日が休日なら何もしない
    if is_holiday(datetime.now()):
        return

    if not SLACK_BOT_TOKEN and not SLACK_WEBHOOK_URL:
        print("[Error] SLACK_BOT_TOKEN または SLACK_WEBHOOK_URL が設定されていません。")
        return

    target_days = get_last_5_business_days()

    absent_students = []

    if not STUDENTS_CSV.exists():
        print(f"[Error] {STUDENTS_CSV} が見つかりません。")
        return

    with open(STUDENTS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["name"]
            slack_id = row.get("slack_id", "").strip()
            if not slack_id:
                continue

            # 出席記録をチェック
            attendance_file = ATTENDANCE_DIR / f"{name}.csv"
            has_attended = False
            
            if attendance_file.exists():
                with open(attendance_file, "r", encoding="utf-8") as af:
                    areader = csv.DictReader(af)
                    # 全記録から「出席」または「リモート中」の日付を抽出
                    attended_dates = [arow["date"] for arow in areader if arow.get("status") in ("出席", "リモート中")]
                    
                    # 直近5営業日に1度でも出席しているか
                    if any(day in attended_dates for day in target_days):
                        has_attended = True
            
            if not has_attended:
                absent_students.append({"name": name, "slack_id": slack_id})

    if absent_students:
        send_notifications(absent_students)

def send_notifications(students):
    """Slackに通知を送信する。"""
    mentions = " ".join([f"<@{s['slack_id']}>" for s in students])
    text = f"{mentions}\nコアタイムが終了しました。直近1週間の出席が確認できていません。明日は来ましょう！"

    if SLACK_BOT_TOKEN:
        try:
            client.chat_postMessage(channel=SLACK_CHANNEL, text=text)
        except SlackApiError as e:
            print(f"Slack API エラー: {e.response['error']}")
    elif SLACK_WEBHOOK_URL:
        import urllib.request
        import json
        message = {"text": text}
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=json.dumps(message).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req) as res:
                pass
        except Exception as e:
            print(f"Webhook送信失敗: {e}")

if __name__ == "__main__":
    check_attendance()
