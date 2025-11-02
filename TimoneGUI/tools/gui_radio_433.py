#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
433 MHz GUI listener
- Subscribes to ZMQ topic "radio433"
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

ALT_RE = re.compile(r"\bALT\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I)
VEL_RE = re.compile(r"\bVEL\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I)
V_RE   = re.compile(r"(?<![A-Z])\bv\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I)

GPS_KV_RE = re.compile(
    r"\bGPS\b.*?lat\s*[:=]\s*([-+]?\d+(?:\.\d+)?)\s*[,;]\s*lng\s*[:=]\s*([-+]?\d+(?:\.\d+)?)",
    re.I,
)
APRS_RE = re.compile(
    r"!\s*(\d{2})(\d{2}\.\d+)\s*([NS])\s*[/\\]\s*(\d{3})(\d{2}\.\d+)\s*([EW])",
    re.I,
)

# Continuity flags
MC_RE = re.compile(r"\bmc\s*[:=]\s*([01])\b", re.I)
DC_RE = re.compile(r"\bdc\s*[:=]\s*([01])\b", re.I)

# NEW: IBIS FSM, RSSI, SNR (from log text)
LS_RE   = re.compile(r"\bLS\s*[:=]\s*(\d{1,3})\b", re.I)
RSSI_RE = re.compile(r"\bRSSI\s*[:=]\s*(-?\d+(?:\.\d+)?)\b", re.I)
SNR_RE  = re.compile(r"\bSNR\s*[:=]\s*(-?\d+(?:\.\d+)?)\b", re.I)

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
    lat = float(lat_deg) + float(lat_min) / 60.0
    lon = float(lon_deg) + float(lon_min) / 60.0
    if lat_hem.upper() == "S": lat = -lat
    if lon_hem.upper() == "W": lon = -lon
    return lat, lon

def parse_telemetry_fields(text: str) -> dict:
    out = {}

    m = ALT_RE.search(text)
    if m: out["alt"] = float(m.group(1))

    vel = None
    m = V_RE.search(text)    # 433 often uses 'v:'
    if m: vel = float(m.group(1))
    if vel is None:
        m = VEL_RE.search(text)
        if m: vel = float(m.group(1))
    if vel is not None: out["vel"] = vel

    m = GPS_KV_RE.search(text)
    if m:
        out["lat"] = float(m.group(1))
        out["lng"] = float(m.group(2))
    else:
        m = APRS_RE.search(text)
        if m:
            lat_deg, lat_min, lat_hem, lon_deg, lon_min, lon_hem = m.groups()
            lat, lon = aprs_to_decimal(lat_deg, lat_min, lat_hem, lon_deg, lon_min, lon_hem)
            out["lat"] = lat
            out["lng"] = lon

    # Optional BARO within radio payloads
    baro_p = re.search(r"\[BARO\].*?\bP\s*=\s*([-+]?\d+(?:\.\d+)?)", text, re.I)
    if baro_p: out["pres"] = float(baro_p.group(1))
    baro_t = re.search(r"\[BARO\].*?\bT\s*=\s*([-+]?\d+(?:\.\d+)?)", text, re.I)
    if baro_t: out["temp"] = float(baro_t.group(1))

    # Continuity flags
    m = MC_RE.search(text)
    if m:
        out["mc"] = int(m.group(1))
        out["main"] = bool(out["mc"])
    m = DC_RE.search(text)
    if m:
        out["dc"] = int(m.group(1))
        out["drog"] = bool(out["dc"])

    # NEW: IBIS FSM, RSSI, SNR
    m = LS_RE.search(text)
    if m:
        try: out["state"] = int(m.group(1))
        except Exception: pass
    m = RSSI_RE.search(text)
    if m:
        try: out["rssi"] = float(m.group(1))
        except Exception: pass
    m = SNR_RE.search(text)
    if m:
        try: out["snr"] = float(m.group(1))
        except Exception: pass

    return out

def main():
    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.connect(PUB_ENDPOINT)
    sub.setsockopt_string(zmq.SUBSCRIBE, "radio433")

    print("[Radio433] listener running; ZMQ:", PUB_ENDPOINT, "GUI:", GUI_BASE)

    while True:
        try:
            topic, raw = sub.recv_multipart()
            msg = json.loads(raw.decode("utf-8"))
            data = msg.get("data", {})

            txt = payload_text(data).strip()
            rssi = data.get("rssi_dbm")  # may or may not be present on 433

            # 1) Log line
            parts = [txt] if txt else []
            if rssi is not None: parts.append(f"(RSSI:{rssi})")
            safe_post(LOGS_PUSH, {"line": f"[Radio433] {' '.join(parts) if parts else '[no payload]'}"})

            # 2) Telemetry row
            if txt:
                row = parse_telemetry_fields(txt)
                # Keep meta RSSI if present and not parsed from text
                if rssi is not None and "rssi" not in row:
                    try: row["rssi"] = float(rssi)
                    except Exception: pass

                if row:
                    row.setdefault("time", int(time.time() * 1000))
                    safe_post(TEL_PUSH, row)
        except Exception:
            pass

if __name__ == "__main__":
    main()
