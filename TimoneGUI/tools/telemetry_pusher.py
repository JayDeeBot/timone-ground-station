#!/usr/bin/env python3
"""
Push telemetry rows from STATE file to the TimoneGUI server on a loop.

Usage:
  python TimoneGUI/tools/telemetry_pusher.py
"""

import csv
import json
import time
import urllib.request
from pathlib import Path

SERVER_URL = "http://127.0.0.1:5000"
PUSH_ENDPOINT = f"{SERVER_URL}/api/telemetry/push"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = PROJECT_ROOT / "src" / "data" / "test_data" / "STATE"

DELAY_SEC = 0.25
PAUSE_BETWEEN_LOOPS = 0.5

# Map CSV headings to our JSON keys
KEY_MAP = {
    "time": "time",
    "state": "state",
    "alt": "alt",
    "vel": "vel",
    "ax": "ax", "ay": "ay", "az": "az",
    "hax": "hax", "hay": "hay", "haz": "haz",
    "pres": "pres",
    "temp": "temp",
    "main": "main",
    "drog": "drog",
    "volts": "volts",
    "curr": "curr",
}

NUM_KEYS = {"time","state","alt","vel","ax","ay","az","hax","hay","haz","pres","temp","main","drog","volts","curr"}

def post_rows(rows):
    data = json.dumps({"rows": rows}).encode("utf-8")
    req = urllib.request.Request(PUSH_ENDPOINT, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()

def to_number(v):
    try:
        if v is None or v == "":
            return None
        # ints for 0/1 flags
        if v.isdigit() or (v.startswith('-') and v[1:].isdigit()):
            return int(v)
        return float(v)
    except Exception:
        return None

def main():
    print(f"[telemetry_pusher] file: {STATE_FILE}")
    print(f"[telemetry_pusher] endpoint: {PUSH_ENDPOINT}")

    while True:
        try:
            with open(STATE_FILE, "r", encoding="utf-8", newline="") as f:
                # Use csv with strict handling but allow empty columns
                reader = csv.reader(f)
                headers = next(reader, [])
                # Normalize headers (skip empties)
                norm = []
                for h in headers:
                    h = (h or "").strip()
                    if not h:
                        norm.append(None)
                    else:
                        norm.append(h)

                batch = []
                for row in reader:
                    obj = {}
                    for h, val in zip(norm, row):
                        if not h:
                            continue  # skip empty columns
                        key = KEY_MAP.get(h)
                        if not key:
                            continue
                        val = val.strip()
                        obj[key] = to_number(val) if key in NUM_KEYS else val
                    # keep only keys we care about
                    if obj:
                        batch.append(obj)

                    # Send in small batches for efficiency
                    if len(batch) >= 10:
                        try:
                            post_rows(batch)
                        except Exception as e:
                            print("[telemetry_pusher] post error:", e)
                        batch = []
                    time.sleep(DELAY_SEC)
                if batch:
                    try:
                        post_rows(batch)
                    except Exception as e:
                        print("[telemetry_pusher] post error:", e)

            time.sleep(PAUSE_BETWEEN_LOOPS)
        except FileNotFoundError:
            print("[telemetry_pusher] STATE file not found; retrying...")
            time.sleep(2.0)
        except KeyboardInterrupt:
            print("\n[telemetry_pusher] stopping")
            break

if __name__ == "__main__":
    main()
