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
GUI LoRa915 Listener → Parser → GUI Pusher
------------------------------------------
- Subscribes to communicator.py PUB bus topic "lora915"
- Parses noisy ASCII bytes in LoRaPacket_t.data into structured telemetry
- Pushes rows to /api/telemetry/push and log lines to /api/logs/push

Refs:
- communicator PUB payload shape & topics: ts/decoded/type/data, topics lora915/radio433/etc.
- telemetry push schema/endpoint mirrors tools/telemetry_pusher.py
- logs push mirrors tools/log_pusher.py
"""
import os, re, json, time, argparse, urllib.request
from typing import Dict, Any, List, Optional
import zmq

SERVER_URL = os.getenv("TIMONE_GUI_URL", "http://127.0.0.1:5000")
TELEM_ENDPOINT = f"{SERVER_URL}/api/telemetry/push"
LOG_ENDPOINT   = f"{SERVER_URL}/api/logs/push"

# ---- Helpers: HTTP ----
def post_json(url: str, payload: Dict[str, Any], timeout=5):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()

def push_rows(rows: List[Dict[str, Any]]):
    if not rows:
        return
    try:
        post_json(TELEM_ENDPOINT, {"rows": rows})
    except Exception as e:
        # Also log the failure to the GUI logs
        try:
            post_json(LOG_ENDPOINT, {"line": f"[LoRa915] telemetry push error: {e}"})
        except Exception:
            pass

def push_log(line: str):
    try:
        post_json(LOG_ENDPOINT, {"line": line})
    except Exception:
        pass

# ---- Helpers: parsing ----
NUM = r"-?\d+(?:\.\d+)?"
RE_SIMPLE_AV   = re.compile(rf"\bALT:(?P<alt>{NUM})\b.*?\bVEL:(?P<vel>{NUM})", re.I)
RE_STATE_ELEC  = re.compile(r"\[State\].*?\bmc:(?P<mc>\d+),\s*dc:(?P<dc>\d+),\s*v:(?P<v>{0}),\s*c:(?P<c>{0})".format(NUM), re.I)
RE_STATE_KIN   = re.compile(r"\[State\].*?\bt:(?P<t>\d+)\s+LS:(?P<ls>\d+)\s+ALT:(?P<alt>{0})\s+VEL:(?P<vel>{0})".format(NUM), re.I)
RE_GPS_STD     = re.compile(r"\[GPS\].*?\bt:(?P<t>{0}),\s*LAT:(?P<lat>{0}),\s*LNG:(?P<lng>{0}),\s*ALT:(?P<alt>{0})".format(NUM), re.I)
RE_APRS        = re.compile(r"APRS.*?:!([0-9]{2,3}[0-9]{2}\.\d+)([NS])\/([0-9]{3}[0-9]{2}\.\d+)([EW])", re.I)

def dmm_to_decimal(dmm: str, hemi: str) -> float:
    """
    Convert APRS ddmm.mm (lat) or dddmm.mm (lon) to signed decimal degrees.
    """
    if len(dmm) < 4:
        return None
    if len(dmm) in (6,7,8,9):  # ddmm.mm or dddmm.mm variants
        # split degrees/minutes: last 2+ fractional are minutes
        if len(dmm) > 6:  # longitude usually 3 deg digits
            deg = int(dmm[:-5])
            mins = float(dmm[-5:])
        else:
            deg = int(dmm[:-5])
            mins = float(dmm[-5:])
    else:
        # fallback: find minutes start by last 5 chars "mm.mm"
        deg = int(dmm[:-5])
        mins = float(dmm[-5:])
    val = deg + mins/60.0
    if hemi.upper() in ("S","W"):
        val = -val
    return val

def to_float(x: Optional[str]) -> Optional[float]:
    try:
        if x is None: return None
        return float(x)
    except Exception:
        return None

def to_int(x: Optional[str]) -> Optional[int]:
    try:
        if x is None: return None
        return int(x)
    except Exception:
        return None

def parse_payload_ascii(s: str) -> Dict[str, Any]:
    """
    Parse one ASCII line into a partial telemetry dict.
    Returns dictionary with any of: time, state, alt, vel, volts, curr, main, drog, lat, lng
    """
    row: Dict[str, Any] = {}

    # 1) Simple ALT/VEL
    m = RE_SIMPLE_AV.search(s)
    if m:
        row["alt"] = to_float(m.group("alt"))
        row["vel"] = to_float(m.group("vel"))

    # 2) [State] electrical (continuities & power)
    m = RE_STATE_ELEC.search(s)
    if m:
        row["main"]  = to_int(m.group("mc"))
        row["drog"]  = to_int(m.group("dc"))
        row["volts"] = to_float(m.group("v"))
        row["curr"]  = to_float(m.group("c"))

    # 3) [State] kinematics (time, LS, alt, vel)
    m = RE_STATE_KIN.search(s)
    if m:
        row["time"]  = to_float(m.group("t"))
        row["state"] = to_int(m.group("ls"))
        row["alt"]   = to_float(m.group("alt"))
        row["vel"]   = to_float(m.group("vel"))

    # 4) [GPS] structured lat/lng
    m = RE_GPS_STD.search(s)
    if m:
        row["time"] = to_float(m.group("t")) if row.get("time") is None else row.get("time")
        row["lat"]  = to_float(m.group("lat"))
        row["lng"]  = to_float(m.group("lng"))
        row["alt"]  = to_float(m.group("alt")) if row.get("alt") is None else row.get("alt")

    # 5) APRS beacon with ddmm.mm/dddmm.mm format
    m = RE_APRS.search(s)
    if m:
        lat_dmm, lat_hemi, lon_dmm, lon_hemi = m.groups()
        row["lat"] = dmm_to_decimal(lat_dmm, lat_hemi)
        row["lng"] = dmm_to_decimal(lon_dmm, lon_hemi)

    return {k: v for k, v in row.items() if v is not None}

# ---- Main ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pub", default=os.getenv("TIMONE_PUB", "tcp://127.0.0.1:5556"),
                    help="PUB endpoint exposed by communicator.py")
    ap.add_argument("--batch", type=int, default=6, help="Telemetry batch size for POST")
    args = ap.parse_args()

    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.connect(args.pub)
    sub.setsockopt_string(zmq.SUBSCRIBE, "lora915")

    push_log(f"[LoRa915] Subscribed to {args.pub} topic lora915")

    buffer: List[Dict[str, Any]] = []
    try:
        while True:
            topic, payload = sub.recv_multipart()
            msg = json.loads(payload.decode("utf-8"))
            data: Dict[str, Any] = msg.get("data", {})
            # Expected from communicator decode: latest ASCII line is in data.get('latest_ascii') or inside decoded text.
            # Fall back to hex if needed.
            ascii_line = data.get("latest_ascii") or data.get("text") or data.get("latest_hex", "")
            if isinstance(ascii_line, bytes):
                try:
                    ascii_line = ascii_line.decode("utf-8", errors="replace")
                except Exception:
                    ascii_line = str(ascii_line)
            if not isinstance(ascii_line, str):
                ascii_line = str(ascii_line)

            # Parse one line (tolerant to noise)
            row = parse_payload_ascii(ascii_line)

            # Attach LoRa link quality if present
            if "snr_db" in data:
                row["snr"] = to_float(str(data.get("snr_db")))
            if "rssi_dbm" in data:
                # store RSSI as a negative number; GUI doesn’t graph it yet, but keep for future
                row["rssi"] = to_float(str(data.get("rssi_dbm")))

            # If we have at least one meaningful value, enqueue + log line
            if row:
                # If no time present, use seconds since start (approx)
                row.setdefault("time", round(time.time() % 100000, 2))
                buffer.append(row)

            # Ship logs to the GUI logs tab (helps trace the raw stream)
            if ascii_line.strip():
                push_log(f"[LoRa915] {ascii_line.strip()}")

            # Flush in small batches
            if len(buffer) >= args.batch:
                push_rows(buffer)
                buffer.clear()
    except KeyboardInterrupt:
        push_log("[LoRa915] Exiting")
    finally:
        try:
            if buffer:
                push_rows(buffer)
        except Exception:
            pass
        sub.close(0)

if __name__ == "__main__":
    main()
