#!/usr/bin/env python3
"""
FeliCa 出席確認システム
========================
使い方:
  .venv/bin/python main.py              → 出席確認 + ダッシュボード同時起動
  .venv/bin/python main.py --register   → カード登録モード
"""

import sys
import threading

from attendance import attendance_mode
from register import register_mode
from dashboard import app
from slack_notifier import check_attendance
from slack_bot import run_slack_bot


def run_dashboard():
    """ダッシュボードをバックグラウンドで起動する。"""
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)


def run_notifier_scheduler():
    """毎日 17:00 に Slack 通知を実行するスケジューラ。"""
    import time
    from datetime import datetime
    
    print(f"\033[94m  [OK] 通知スケジューラ起動: 毎日 17:00 (平日のみ)\033[0m")
    
    last_run_date = ""
    
    while True:
        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")
        
        # 祝日・カスタム休日（夏休み等）を含めた休日判定
        from slack_notifier import is_holiday
        
        # 休日でなく、かつ 17:00台 かつ 今日まだ実行していない場合
        if not is_holiday(now) and now.hour == 17 and current_date != last_run_date:
            try:
                check_attendance()
                last_run_date = current_date
            except Exception as e:
                print(f"\033[91m  [エラー] 通知実行に失敗しました: {e}\033[0m")
        
        # 1分おきにチェック
        time.sleep(60)


def main():
    if "--register" in sys.argv or "-r" in sys.argv:
        register_mode()
    else:
        # ダッシュボードをバックグラウンドスレッドで起動
        dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
        dashboard_thread.start()
        print(f"\033[96m  [OK] ダッシュボード起動: http://localhost:5000\033[0m")

        # 通知スケジューラをバックグラウンドスレッドで起動
        notifier_thread = threading.Thread(target=run_notifier_scheduler, daemon=True)
        notifier_thread.start()

        # Slack Bot (Socket Mode) をバックグラウンドスレッドで起動
        slack_bot_thread = threading.Thread(target=run_slack_bot, daemon=True)
        slack_bot_thread.start()
        print()

        # 出席確認モードをメインスレッドで実行
        attendance_mode()


if __name__ == "__main__":
    main()
