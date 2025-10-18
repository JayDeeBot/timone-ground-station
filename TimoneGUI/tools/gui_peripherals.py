### --- UNCOMMENT FOR LOGGING ONLY VERSION --- ###

# #!/usr/bin/env python3
# # -*- coding: utf-8 -*-
# """
# GUI Peripherals Listener
# ------------------------
# Subscribes to barometer/current (and optionally raw for future externals) and prints updates.
# Run:
#   python3 gui_peripherals.py --pub tcp://127.0.0.1:5556
# """
# import os
# import sys
# import json
# import time
# import argparse
# import zmq

# def main():
#     ap = argparse.ArgumentParser()
#     ap.add_argument("--pub", default=os.getenv("TIMONE_PUB", "tcp://127.0.0.1:5556"),
#                     help="PUB endpoint exposed by communicator.py")
#     ap.add_argument("--include-raw", action="store_true",
#                     help="Also subscribe to 'raw' for unknown/external peripherals")
#     args = ap.parse_args()

#     ctx = zmq.Context.instance()
#     sub = ctx.socket(zmq.SUB)
#     sub.connect(args.pub)
#     sub.setsockopt_string(zmq.SUBSCRIBE, "barometer")
#     sub.setsockopt_string(zmq.SUBSCRIBE, "current")
#     if args.include_raw:
#         sub.setsockopt_string(zmq.SUBSCRIBE, "raw")

#     print(f"[PERIPH] Connected to {args.pub}, topics: barometer, current"
#           + (", raw" if args.include_raw else ""))
#     try:
#         while True:
#             topic, payload = sub.recv_multipart()
#             topic = topic.decode("utf-8")
#             msg = json.loads(payload.decode("utf-8"))
#             ts = msg.get("ts", int(time.time()*1000))
#             data = msg.get("data", {})

#             if topic == "barometer":
#                 print(f"[BARO] ts={ts} P={data.get('pressure_hpa')} hPa  T={data.get('temperature_c')} °C "
#                       f"Alt={data.get('altitude_m')} m")
#             elif topic == "current":
#                 print(f"[CURR] ts={ts} I={data.get('current_a')} A  V={data.get('voltage_v')} V  "
#                       f"P={data.get('power_w')} W  raw_adc={data.get('raw_adc')}")
#             else:
#                 # raw / unknown external
#                 print(f"[RAW] ts={ts} pid={msg.get('peripheral_id')} decoded={msg.get('decoded')} "
#                       f"type={msg.get('type')} len={data.get('len')} hex={data.get('payload_hex')}")
#             print("-"*60)
#     except KeyboardInterrupt:
#         print("\n[PERIPH] Exiting...")
#     finally:
#         sub.close(0)

# if __name__ == "__main__":
#     main()

### ---- FULL GUI VERSION BELOW ---- ###

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI Peripherals Listener → Parser → GUI Pusher
----------------------------------------------
Subscribes to communicator.py PUB bus for:
  - "barometer"  → BarometerData_t
  - "current"    → CurrentData_t

Maps messages into GUI telemetry rows and pushes to /api/telemetry/push.
Also mirrors concise messages to /api/logs/push for visibility.

Environment:
  TIMONE_PUB     (default tcp://127.0.0.1:5556)
  TIMONE_GUI_URL (default http://127.0.0.1:5000)

Telemetry row keys used (GUI-friendly & future-proof):
  time (s), volts (V), curr (A), power (W),
  baro_press (hPa), baro_temp (C), baro_alt (m)

Notes:
- If your GUI’s charts don’t yet bind to baro_* or power, they’ll be safely ignored
  until you add them in telemetry.js. They won’t break existing plots.
"""
import os, json, time, argparse, urllib.request
from typing import Dict, Any, List, Optional
import zmq

SERVER_URL = os.getenv("TIMONE_GUI_URL", "http://127.0.0.1:5000")
TELEM_ENDPOINT = f"{SERVER_URL}/api/telemetry/push"
LOG_ENDPOINT   = f"{SERVER_URL}/api/logs/push"

# ---------------- HTTP helpers ----------------
def _post_json(url: str, payload: Dict[str, Any], timeout=5):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()

def push_rows(rows: List[Dict[str, Any]]):
    if not rows:
        return
    try:
        _post_json(TELEM_ENDPOINT, {"rows": rows})
    except Exception as e:
        push_log(f"[Peripherals] telemetry push error: {e}")

def push_log(line: str):
    try:
        _post_json(LOG_ENDPOINT, {"line": line})
    except Exception:
        pass

# ---------------- Mapping helpers ----------------
def as_float(x: Optional[float]) -> Optional[float]:
    try:
        if x is None: return None
        return float(x)
    except Exception:
        return None

def as_int(x: Optional[int]) -> Optional[int]:
    try:
        if x is None: return None
        return int(x)
    except Exception:
        return None

def map_barometer(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    BarometerData_t:
      uint32_t timestamp;   (assume ms since boot or s; we normalize to seconds float)
      float pressure_hpa;
      float temperature_c;
      float altitude_m;
    """
    ts = payload.get("timestamp")
    # Heuristic: treat big values as ms, small as s
    if isinstance(ts, (int, float)) and ts > 1e6:
        t_secs = round(float(ts) / 1000.0, 2)
    else:
        t_secs = round(float(ts or 0), 2)

    row = {
        "time":       t_secs,
        "baro_press": as_float(payload.get("pressure_hpa")),
        "baro_temp":  as_float(payload.get("temperature_c")),
        "baro_alt":   as_float(payload.get("altitude_m")),
    }
    return {k: v for k, v in row.items() if v is not None}

def map_current(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    CurrentData_t:
      uint32_t timestamp; (same normalization as above)
      float current_a;
      float voltage_v;
      float power_w;
    """
    ts = payload.get("timestamp")
    if isinstance(ts, (int, float)) and ts > 1e6:
        t_secs = round(float(ts) / 1000.0, 2)
    else:
        t_secs = round(float(ts or 0), 2)

    row = {
        "time":  t_secs,
        "curr":  as_float(payload.get("current_a")),
        "volts": as_float(payload.get("voltage_v")),
        "power": as_float(payload.get("power_w")),
    }
    return {k: v for k, v in row.items() if v is not None}

# ---------------- Main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pub", default=os.getenv("TIMONE_PUB", "tcp://127.0.0.1:5556"),
                    help="PUB endpoint exposed by communicator.py")
    ap.add_argument("--batch", type=int, default=6, help="Telemetry batch size for POST")
    args = ap.parse_args()

    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.connect(args.pub)
    # Subscribe to both topics
    sub.setsockopt_string(zmq.SUBSCRIBE, "barometer")
    sub.setsockopt_string(zmq.SUBSCRIBE, "current")

    push_log(f"[Peripherals] Subscribed to {args.pub} topics: barometer, current")

    buffer: List[Dict[str, Any]] = []

    try:
        while True:
            topic_b, payload_b = sub.recv_multipart()
            topic = topic_b.decode("utf-8", errors="replace")
            try:
                msg = json.loads(payload_b.decode("utf-8"))
            except Exception:
                # If communicator publishes a tuple-like JSON, wrap fallback
                msg = {}

            data = msg.get("data", msg)  # accept either {"data":{...}} or flat object

            if topic == "barometer":
                row = map_barometer(data)
                if row:
                    buffer.append(row)
                    push_log(f"[Barometer] P={row.get('baro_press')} hPa, T={row.get('baro_temp')}°C, ALT={row.get('baro_alt')} m @t={row.get('time')}")
            elif topic == "current":
                row = map_current(data)
                if row:
                    buffer.append(row)
                    push_log(f"[Current] V={row.get('volts')} V, I={row.get('curr')} A, P={row.get('power')} W @t={row.get('time')}")

            if len(buffer) >= args.batch:
                flush, buffer = buffer, []
                push_rows(flush)

    except KeyboardInterrupt:
        push_log("[Peripherals] Exiting")
    finally:
        try:
            if buffer:
                push_rows(buffer)
        except Exception:
            pass
        sub.close(0)

if __name__ == "__main__":
    main()
