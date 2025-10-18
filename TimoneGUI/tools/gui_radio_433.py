### ----------- UNCOMMENT FOR LOGGING ONLY VERSION ----------- ###

# #!/usr/bin/env python3
# # -*- coding: utf-8 -*-
# """
# GUI Radio433 Listener
# ---------------------
# Subscribes to the communicator PUB bus and prints 433 MHz telemetry.
# Run:
#   python3 gui_radio_433.py --pub tcp://127.0.0.1:5556
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
#     sub.setsockopt_string(zmq.SUBSCRIBE, "radio433")

#     print(f"[RADIO433] Connected to {args.pub}, subscribed to topic 'radio433'")
#     try:
#         while True:
#             topic, payload = sub.recv_multipart()
#             msg = json.loads(payload.decode("utf-8"))
#             ts = msg.get("ts", int(time.time()*1000))
#             data = msg.get("data", {})
#             print(f"[RADIO433] ts={ts} decoded={msg.get('decoded')} type={msg.get('type')}")
#             print(f"  packet_count={data.get('packet_count')} rssi_dbm={data.get('rssi_dbm')} "
#                   f"latest_len={data.get('latest_len')}")
#             if "latest_hex" in data:
#                 print(f"  latest_hex={data['latest_hex']}")
#             print("-"*60)
#     except KeyboardInterrupt:
#         print("\n[RADIO433] Exiting...")
#     finally:
#         sub.close(0)

# if __name__ == "__main__":
#     main()


### ----------- FULL GUI VERSION ----------- ###

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI Radio433 Listener → Parser → GUI Pusher
-------------------------------------------
- Subscribes to communicator.py PUB bus topic "radio433"
- Parses noisy ASCII bytes in Radio433Packet_t.data into structured telemetry
- Pushes rows to /api/telemetry/push and log lines to /api/logs/push
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
        try:
            post_json(LOG_ENDPOINT, {"line": f"[Radio433] telemetry push error: {e}"})
        except Exception:
            pass

def push_log(line: str):
    try:
        post_json(LOG_ENDPOINT, {"line": line})
    except Exception:
        pass

# ---- Helpers: parsing (shared with 915) ----
NUM = r"-?\d+(?:\.\d+)?"
RE_SIMPLE_AV   = re.compile(rf"\bALT:(?P<alt>{NUM})\b.*?\bVEL:(?P<vel>{NUM})", re.I)
RE_STATE_ELEC  = re.compile(r"\[State\].*?\bmc:(?P<mc>\d+),\s*dc:(?P<dc>\d+),\s*v:(?P<v>{0}),\s*c:(?P<c>{0})".format(NUM), re.I)
RE_STATE_KIN   = re.compile(r"\[State\].*?\bt:(?P<t>\d+)\s+LS:(?P<ls>\d+)\s+ALT:(?P<alt>{0})\s+VEL:(?P<vel>{0})".format(NUM), re.I)
RE_GPS_STD     = re.compile(r"\[GPS\].*?\bt:(?P<t>{0}),\s*LAT:(?P<lat>{0}),\s*LNG:(?P<lng>{0}),\s*ALT:(?P<alt>{0})".format(NUM), re.I)
RE_APRS        = re.compile(r"APRS.*?:!([0-9]{2,3}[0-9]{2}\.\d+)([NS])\/([0-9]{3}[0-9]{2}\.\d+)([EW])", re.I)

def dmm_to_decimal(dmm: str, hemi: str) -> float:
    if len(dmm) < 4:
        return None
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
    row: Dict[str, Any] = {}

    m = RE_SIMPLE_AV.search(s)
    if m:
        row["alt"] = to_float(m.group("alt"))
        row["vel"] = to_float(m.group("vel"))

    m = RE_STATE_ELEC.search(s)
    if m:
        row["main"]  = to_int(m.group("mc"))
        row["drog"]  = to_int(m.group("dc"))
        row["volts"] = to_float(m.group("v"))
        row["curr"]  = to_float(m.group("c"))

    m = RE_STATE_KIN.search(s)
    if m:
        row["time"]  = to_float(m.group("t"))
        row["state"] = to_int(m.group("ls"))
        row["alt"]   = to_float(m.group("alt"))
        row["vel"]   = to_float(m.group("vel"))

    m = RE_GPS_STD.search(s)
    if m:
        row["time"] = to_float(m.group("t")) if row.get("time") is None else row.get("time")
        row["lat"]  = to_float(m.group("lat"))
        row["lng"]  = to_float(m.group("lng"))
        row["alt"]  = to_float(m.group("alt")) if row.get("alt") is None else row.get("alt")

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
    sub.setsockopt_string(zmq.SUBSCRIBE, "radio433")

    push_log(f"[Radio433] Subscribed to {args.pub} topic radio433")

    buffer: List[Dict[str, Any]] = []
    try:
        while True:
            topic, payload = sub.recv_multipart()
            msg = json.loads(payload.decode("utf-8"))
            data: Dict[str, Any] = msg.get("data", {})
            ascii_line = data.get("latest_ascii") or data.get("text") or data.get("latest_hex", "")
            if isinstance(ascii_line, bytes):
                try:
                    ascii_line = ascii_line.decode("utf-8", errors="replace")
                except Exception:
                    ascii_line = str(ascii_line)
            if not isinstance(ascii_line, str):
                ascii_line = str(ascii_line)

            row = parse_payload_ascii(ascii_line)
            # For 433 we don't expect snr, but propagate RSSI if present
            if "rssi_dbm" in data:
                row["rssi"] = to_float(str(data.get("rssi_dbm")))

            if row:
                row.setdefault("time", round(time.time() % 100000, 2))
                buffer.append(row)

            if ascii_line.strip():
                push_log(f"[Radio433] {ascii_line.strip()}")

            if len(buffer) >= args.batch:
                push_rows(buffer)
                buffer.clear()
    except KeyboardInterrupt:
        push_log("[Radio433] Exiting")
    finally:
        try:
            if buffer:
                push_rows(buffer)
        except Exception:
            pass
        sub.close(0)

if __name__ == "__main__":
    main()
