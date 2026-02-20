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


def run_dashboard():
    """ダッシュボードをバックグラウンドで起動する。"""
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)


def main():
    if "--register" in sys.argv or "-r" in sys.argv:
        register_mode()
    else:
        # ダッシュボードをバックグラウンドスレッドで起動
        dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
        dashboard_thread.start()
        print(f"\033[96m  [OK] ダッシュボード起動: http://localhost:5000\033[0m")
        print()

        # 出席確認モードをメインスレッドで実行
        attendance_mode()


if __name__ == "__main__":
    main()
