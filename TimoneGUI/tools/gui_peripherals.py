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
Peripherals GUI listener
- Subscribes to ZMQ topics "barometer" and "current"
- Logs concise lines
- POSTS telemetry rows with baro_* (mapped by telemetry.js normalizer)
"""

import os
import json
import time
import zmq
import requests

PUB_ENDPOINT = os.getenv("TIMONE_PUB", "tcp://127.0.0.1:5556")
GUI_BASE     = os.getenv("TIMONE_GUI", "http://127.0.0.1:5000")

LOGS_PUSH = f"{GUI_BASE}/api/logs/push"
TEL_PUSH  = f"{GUI_BASE}/api/telemetry/push"

def safe_post(url, payload, timeout=2.0):
  try:
    requests.post(url, json=payload, timeout=timeout)
  except Exception:
    pass

def main():
  ctx = zmq.Context.instance()
  sub = ctx.socket(zmq.SUB)
  sub.connect(PUB_ENDPOINT)
  sub.setsockopt_string(zmq.SUBSCRIBE, "barometer")
  sub.setsockopt_string(zmq.SUBSCRIBE, "current")

  print("[Peripherals] listener running; ZMQ:", PUB_ENDPOINT, "GUI:", GUI_BASE)

  while True:
    try:
      topic, raw = sub.recv_multipart()
      topic = topic.decode("utf-8")
      msg = json.loads(raw.decode("utf-8"))
      data = msg.get("data", {})

      if topic == "barometer":
        p = data.get("pressure_hpa")
        t = data.get("temperature_c")
        alt = data.get("altitude_m")
        # log
        parts = []
        if p is not None: parts.append(f"P={p:.3f} hPa")
        if t is not None: parts.append(f"T={t:.3f}°C")
        if alt is not None: parts.append(f"ALT={alt:.2f} m")
        if parts: safe_post(LOGS_PUSH, {"line": "[Barometer] " + " ".join(parts)})

        # telemetry row (use baro_* keys; telemetry.js maps -> pres/temp/alt)
        row = {}
        if p is not None:   row["baro_press"] = float(p)
        if t is not None:   row["baro_temp"]  = float(t)
        if alt is not None: row["baro_alt"]   = float(alt)
        if row:
          row.setdefault("time", int(time.time()*1000))
          safe_post(TEL_PUSH, row)

      elif topic == "current":
        ia = data.get("current_a")
        vv = data.get("voltage_v")
        pw = data.get("power_w")
        # log
        parts = []
        if vv is not None: parts.append(f"VBAT={vv:.2f} V")
        if ia is not None: parts.append(f"IBAT={ia:.2f} A")
        if pw is not None: parts.append(f"P={pw:.1f} W")
        if parts: safe_post(LOGS_PUSH, {"line": "[Current] " + " ".join(parts)})

        # telemetry row (volts/curr expected by UI)
        row = {}
        if vv is not None: row["volts"] = float(vv)
        if ia is not None: row["curr"]  = float(ia)
        if row:
          row.setdefault("time", int(time.time()*1000))
          safe_post(TEL_PUSH, row)

    except KeyboardInterrupt:
      break
    except Exception as e:
      safe_post(LOGS_PUSH, {"line": f"[Peripherals] error: {e}"})
      time.sleep(0.25)

if __name__ == "__main__":
  main()
