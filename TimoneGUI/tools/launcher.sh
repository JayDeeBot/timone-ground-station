#!/bin/bash
# ----------------------------------------------------------
# launcher.sh
# Launch the TimoneGUI System including:
# 1. Web app
# 2. Communication stack
# 3. Firefox with webapp url

# Dependencies:
# For Chrome Install: sudo apt install chromium-browser xdotool -y
# For Firefox Install: sudo apt install wmctrl xdotool -y
# ----------------------------------------------------------

# Wait a few seconds for the desktop environment to start
sleep 5

# --- configuration ---
URL="http://127.0.0.1:5000"
cd ~/git/timone-ground-station/TimoneGUI || exit

# 1. Start the communication stack
# python3 tools/run_all.py &
# sleep 2

# 2. Launch the web app
python3 src/app.py &
sleep 2

# 3. Launch the browser with the web app URL

# # Launch Firefox with the desired URL
# # -new-window opens it in a clean window
# # & runs it in the background
# firefox --new-window "$URL" &

# Launch Chromium with the desired URL
# --start-maximized: open window maximized
# --noerrdialogs: suppress crash restore dialogs
# --disable-infobars: remove "Chrome is being controlled" message
# & runs it in the background
chromium-browser --noerrdialogs --disable-infobars --start-maximized "$URL" &

# Give browser a moment to start before trying to manipulate the window
sleep 3

# Force full-screen (F11 equivalent):
# xdotool search --onlyvisible --class "firefox" key F11 # Firefox version
xdotool search --onlyvisible --class "chromium" key F11 # Chromium version
