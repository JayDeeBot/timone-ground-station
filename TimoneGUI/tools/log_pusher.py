#!/usr/bin/env python3
"""
Push test log lines to the TimoneGUI server, looping forever.

Usage:
  python tools/log_pusher.py
  (ensure the Flask app is running on http://127.0.0.1:5000)
"""

import time
import json
import urllib.request
from pathlib import Path

# Adjust if your server runs elsewhere
SERVER_URL = "http://127.0.0.1:5000"
PUSH_ENDPOINT = f"{SERVER_URL}/api/logs/push"

# Your test file (no extension)
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # TimoneGUI/
TEST_LOG = PROJECT_ROOT / "src" / "data" / "test_data" / "goanna flight log"

DELAY_SEC = 3.0  # pace between lines
PAUSE_BETWEEN_LOOPS = 0.5

def push_line(line: str):
    data = json.dumps({"line": line}).encode("utf-8")
    req = urllib.request.Request(
        PUSH_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()

def main():
    print(f"[pusher] Using file: {TEST_LOG}")
    print(f"[pusher] Pushing to : {PUSH_ENDPOINT}")
    while True:
        try:
            with open(TEST_LOG, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    line = raw.rstrip("\r\n")
                    if not line:
                        continue
                    try:
                        push_line(line)
                    except Exception as e:
                        print(f"[pusher] push error: {e}")
                        time.sleep(1.0)
                        # retry next line; don't exit
                    time.sleep(DELAY_SEC)
            time.sleep(PAUSE_BETWEEN_LOOPS)
        except FileNotFoundError:
            print("[pusher] file not found; waiting...")
            time.sleep(2.0)
        except KeyboardInterrupt:
            print("\n[pusher] stopping")
            break

if __name__ == "__main__":
    main()
