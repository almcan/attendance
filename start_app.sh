#!/bin/bash

# Move to the project directory
cd /home/d-102/Attendance

# Activate the virtual environment and run the app
# Using nohup to ensuring it keeps running even if the terminal closes (though desktop entry handles this usually)
# Logging stdout/stderr to a file for debugging
./.venv/bin/python main.py > attendance.log 2>&1 &

# Store the Process ID of the application
APP_PID=$!

# Wait for the server to start (simple sleep for now, could be more robust with curl loop)
sleep 5

# Launch the browser in kiosk mode
# --kiosk: Full screen mode
# --noerrdialogs: Suppress error dialogs
# --disable-infobars: Remove "Chrome is being controlled by automated software" etc
# --check-for-update-interval=31536000: Disable update checks
chromium --kiosk --noerrdialogs --disable-infobars --check-for-update-interval=31536000 --password-store=basic http://localhost:5000 &

# Wait for the app process (optional, keeps the script running if needed for monitoring)
wait $APP_PID
