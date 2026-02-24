#!/bin/bash
# ─── 出席確認システム 起動スクリプト ───
# main.py は systemd (attendance.service) が管理するため、
# このスクリプトはブラウザのキオスク起動のみを担当する。

cd /home/d-102/Attendance

# systemd サービスが起動していなければ開始
sudo systemctl start attendance.service 2>/dev/null

# ダッシュボードの起動を待つ
echo "ダッシュボードの起動を待っています..."
for i in $(seq 1 30); do
    if curl -s http://localhost:5000 > /dev/null 2>&1; then
        echo "ダッシュボード起動確認OK"
        break
    fi
    sleep 1
done

# Launch the browser in kiosk mode
chromium --kiosk --noerrdialogs --disable-infobars --check-for-update-interval=31536000 --password-store=basic http://localhost:5000 &
