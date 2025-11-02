#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI System Status Listener → GUI Pusher
---------------------------------------
Subscribes to communicator.py PUB bus for:
  - "status" → SystemStatus_t
  - "barometer" → Barometer data
  - "current" → Current/Voltage data

Attempts to POST the full system snapshot to /api/status/push.
Always mirrors a concise, human-readable status line to /api/logs/push.
"""
import os, json, time, argparse, urllib.request
from typing import Dict, Any
import zmq

SERVER_URL = os.getenv("TIMONE_GUI_URL", "http://127.0.0.1:5000")
STATUS_ENDPOINT = f"{SERVER_URL}/api/status/push"
LOG_ENDPOINT = f"{SERVER_URL}/api/logs/push"
TEL_ENDPOINT = f"{SERVER_URL}/api/telemetry/push"

# ------------- HTTP helpers -------------
def _post_json(url: str, payload: Dict[str, Any], timeout=5):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()

def push_status(snapshot: Dict[str, Any]):
    try:
        _post_json(STATUS_ENDPOINT, snapshot)
    except Exception:
        # Endpoint may not exist yet in app.py — that’s OK; we still log the status line below.
        pass

def push_log(line: str):
    try:
        _post_json(LOG_ENDPOINT, {"line": line})
    except Exception:
        pass

# ------------- Mapping -------------
def normalize_status(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert SystemStatus_t-like dict into a JSON snapshot the GUI can display.
    We keep the field names intuitive and stable.
    """
    # Some communicators publish enums as ints — keep both "system_state" (raw) and "state_name" (if available later)
    snapshot = {
        "system_state":        data.get("system_state"),
        "wakeup_time":         data.get("wakeup_time"),
        "lora_online":         bool(data.get("lora_online")),
        "radio433_online":     bool(data.get("radio433_online")),
        "barometer_online":    bool(data.get("barometer_online")),
        "current_sensor_online": bool(data.get("current_sensor_online")),
        "pi_connected":        bool(data.get("pi_connected")),
        "uptime_seconds":      int(data.get("uptime_seconds") or 0),
        "packet_count_lora":   int(data.get("packet_count_lora") or 0),
        "packet_count_433":    int(data.get("packet_count_433") or 0),
        # Add a timestamp the GUI could show for “last update”
        "received_at":         int(time.time()),
    }
    return snapshot

def status_line(snapshot: Dict[str, Any]) -> str:
    flags = []
    flags.append(f"LoRa={'ON' if snapshot.get('lora_online') else 'OFF'}")
    flags.append(f"433={'ON' if snapshot.get('radio433_online') else 'OFF'}")
    flags.append(f"BARO={'ON' if snapshot.get('barometer_online') else 'OFF'}")
    flags.append(f"CURR={'ON' if snapshot.get('current_sensor_online') else 'OFF'}")
    flags.append(f"PI={'ON' if snapshot.get('pi_connected') else 'OFF'}")
    return (f"[Status] state={snapshot.get('system_state')}  "
            f"uptime={snapshot.get('uptime_seconds')}s  "
            f"pkts(915/433)={snapshot.get('packet_count_lora')}/{snapshot.get('packet_count_433')}  "
            + " ".join(flags))

# ------------- Main -------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pub", default=os.getenv("TIMONE_PUB", "tcp://127.0.0.1:5556"),
                    help="PUB endpoint exposed by communicator.py")
    args = ap.parse_args()

    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.connect(args.pub)
    sub.setsockopt_string(zmq.SUBSCRIBE, "status")
    sub.setsockopt_string(zmq.SUBSCRIBE, "barometer")  # Add barometer subscription
    sub.setsockopt_string(zmq.SUBSCRIBE, "current")    # Add current subscription

    push_log(f"[Status] Subscribed to {args.pub} topics: status, barometer, current")

    try:
        while True:
            topic_b, payload_b = sub.recv_multipart()
            topic = topic_b.decode("utf-8")
            try:
                msg = json.loads(payload_b.decode("utf-8"))
            except Exception:
                msg = {}

            if topic == "barometer":
                data = msg.get("data", {})
                p = data.get("pressure_hpa")
                t = data.get("temperature_c")
                
                if p is not None or t is not None:
                    # Send telemetry first
                    tel_data = {
                        "type": "baro",  # Add type to help identify data
                        "temp": float(t) if t is not None else 0,
                        "pres": float(p)/10.0 if p is not None else 0  # Convert hPa to kPa
                    }
                    print(f"Sending telemetry: {tel_data}")  # Debug print
                    _post_json(TEL_ENDPOINT, tel_data)
                    
                    # Then log
                    parts = []
                    if p is not None: parts.append(f"P={p:.3f} hPa")
                    if t is not None: parts.append(f"T={t:.3f}°C")
                    if parts:
                        push_log("[BARO] " + " ".join(parts))

            elif topic == "current":
                data = msg.get("data", {})
                ia = data.get("current_a")
                vv = data.get("voltage_v")
                pw = data.get("power_w")
                # Log
                parts = []
                if vv is not None: parts.append(f"VBAT={vv:.2f} V")
                if ia is not None: parts.append(f"IBAT={ia:.2f} A")
                if pw is not None: parts.append(f"P={pw:.1f} W")
                if parts: 
                    push_log("[CURR] " + " ".join(parts))
                    # Send telemetry
                    tel_data = {
                        "time": int(time.time()*1000),
                        "volts": float(vv) if vv is not None else 0,
                        "curr": float(ia) if ia is not None else 0
                    }
                    _post_json(TEL_ENDPOINT, tel_data)

            elif topic == "status":
                data = msg.get("data", msg)
                snap = normalize_status(data)
                push_status(snap)
                push_log(status_line(snap))

    except KeyboardInterrupt:
        push_log("[Status] Exiting")
    finally:
        sub.close(0)

if __name__ == "__main__":
    main()
