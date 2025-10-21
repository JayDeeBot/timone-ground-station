### --------- UNCOMMENT FOR LOGGING ONLY VERSION --------- ###

# #!/usr/bin/env python3
# # -*- coding: utf-8 -*-
# """
# GUI Lora915 Listener
# --------------------
# Subscribes to the communicator PUB bus and prints LoRa 915 telemetry.
# Run:
#   python3 gui_lora_915.py --pub tcp://127.0.0.1:5556
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
#     args = ap.parse_args()

#     ctx = zmq.Context.instance()
#     sub = ctx.socket(zmq.SUB)
#     sub.connect(args.pub)
#     sub.setsockopt_string(zmq.SUBSCRIBE, "lora915")

#     print(f"[LORA915] Connected to {args.pub}, subscribed to topic 'lora915'")
#     try:
#         while True:
#             topic, payload = sub.recv_multipart()
#             msg = json.loads(payload.decode("utf-8"))
#             ts = msg.get("ts", int(time.time()*1000))
#             data = msg.get("data", {})
#             print(f"[LORA915] ts={ts} decoded={msg.get('decoded')} type={msg.get('type')}")
#             print(f"  packet_count={data.get('packet_count')} rssi_dbm={data.get('rssi_dbm')} "
#                   f"snr_db={data.get('snr_db')} latest_len={data.get('latest_len')}")
#             if "latest_hex" in data:
#                 print(f"  latest_hex={data['latest_hex']}")
#             print("-"*60)
#     except KeyboardInterrupt:
#         print("\n[LORA915] Exiting...")
#     finally:
#         sub.close(0)

# if __name__ == "__main__":
#     main()

### --------- FULL GUI VERSION --------- ###

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LoRa915 GUI listener
- Subscribes to ZMQ topic "lora915"
- Logs readable lines and POSTS normalized telemetry rows to the GUI
"""

import os
import re
import json
import time
import zmq
import requests

PUB_ENDPOINT = os.getenv("TIMONE_PUB", "tcp://127.0.0.1:5556")
GUI_BASE     = os.getenv("TIMONE_GUI", "http://127.0.0.1:5000")

LOGS_PUSH = f"{GUI_BASE}/api/logs/push"
TEL_PUSH  = f"{GUI_BASE}/api/telemetry/push"

# --- Regexes for telemetry fields in payload text ---
ALT_RE = re.compile(r"\bALT\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I)
VEL_RE = re.compile(r"\bVEL\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I)
V_RE   = re.compile(r"(?<![A-Z])\bv\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I)  # 'v:' variant

# Flexible [GPS] LAT/LNG (case/spacing tolerant, : or =, , or ; separators)
GPS_KV_RE = re.compile(
    r"\bGPS\b.*?lat\s*[:=]\s*([-+]?\d+(?:\.\d+)?)\s*[,;]\s*lng\s*[:=]\s*([-+]?\d+(?:\.\d+)?)",
    re.I,
)

# APRS position like: !3040.11S/14311.52E  (optional trailing symbol)
APRS_RE = re.compile(
    r"!\s*(\d{2})(\d{2}\.\d+)\s*([NS])\s*[/\\]\s*(\d{3})(\d{2}\.\d+)\s*([EW])",
    re.I,
)

def safe_post(url, payload, timeout=2.0):
    try:
        requests.post(url, json=payload, timeout=timeout)
    except Exception:
        pass

def payload_text(data: dict) -> str:
    txt = data.get("latest_ascii") or ""
    if txt:
        return txt
    hx = data.get("latest_hex", "")
    if hx:
        try:
            return bytes.fromhex(hx).decode("utf-8", errors="replace")
        except Exception:
            return hx
    return ""

def aprs_to_decimal(lat_deg, lat_min, lat_hem, lon_deg, lon_min, lon_hem):
    # lat_deg: 2 digits, lon_deg: 3 digits; minutes are decimal minutes
    lat = float(lat_deg) + float(lat_min) / 60.0
    lon = float(lon_deg) + float(lon_min) / 60.0
    if lat_hem.upper() == "S": lat = -lat
    if lon_hem.upper() == "W": lon = -lon
    return lat, lon

def parse_telemetry_fields(text: str) -> dict:
    out = {}

    m = ALT_RE.search(text)
    if m: out["alt"] = float(m.group(1))

    # prefer VEL, fallback to v:
    vel = None
    m = VEL_RE.search(text)
    if m: vel = float(m.group(1))
    if vel is None:
        m = V_RE.search(text)
        if m: vel = float(m.group(1))
    if vel is not None: out["vel"] = vel

    # GPS via [GPS] LAT/LNG
    m = GPS_KV_RE.search(text)
    if m:
        out["lat"] = float(m.group(1))
        out["lng"] = float(m.group(2))
    else:
        # APRS format
        m = APRS_RE.search(text)
        if m:
            lat_deg, lat_min, lat_hem, lon_deg, lon_min, lon_hem = m.groups()
            lat, lon = aprs_to_decimal(lat_deg, lat_min, lat_hem, lon_deg, lon_min, lon_hem)
            out["lat"] = lat
            out["lng"] = lon

    # BARO (pressure / temp) inside radio payloads (optional)
    baro_p = re.search(r"\[BARO\].*?\bP\s*=\s*([-+]?\d+(?:\.\d+)?)", text, re.I)
    if baro_p: out["pres"] = float(baro_p.group(1))
    baro_t = re.search(r"\[BARO\].*?\bT\s*=\s*([-+]?\d+(?:\.\d+)?)", text, re.I)
    if baro_t: out["temp"] = float(baro_t.group(1))

    return out

def main():
    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.connect(PUB_ENDPOINT)
    sub.setsockopt_string(zmq.SUBSCRIBE, "lora915")

    print("[LoRa915] listener running; ZMQ:", PUB_ENDPOINT, "GUI:", GUI_BASE)

    while True:
        try:
            topic, raw = sub.recv_multipart()
            msg = json.loads(raw.decode("utf-8"))
            data = msg.get("data", {})

            txt = payload_text(data).strip()
            rssi = data.get("rssi_dbm")
            snr  = data.get("snr_db")

            # 1) Log line
            parts = [txt] if txt else []
            meta = []
            if rssi is not None: meta.append(f"RSSI:{rssi}")
            if snr  is not None: meta.append(f"SNR:{snr}")
            if meta: parts.append("(" + ", ".join(meta) + ")")
            safe_post(LOGS_PUSH, {"line": f"[LoRa915] {' '.join(parts) if parts else '[no payload]'}"})

            # 2) Telemetry row (any fields we find)
            if txt:
                row = parse_telemetry_fields(txt)
                if row:
                    row.setdefault("time", int(time.time() * 1000))
                    safe_post(TEL_PUSH, row)

        except KeyboardInterrupt:
            break
        except Exception as e:
            safe_post(LOGS_PUSH, {"line": f"[LoRa915] error: {e}"})
            time.sleep(0.25)

if __name__ == "__main__":
    main()
