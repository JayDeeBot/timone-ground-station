### -------------- UNCOMMENT FOR LOGGING ONLY VERSION -------------- ###
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
#     # Status + heartbeat are useful here
#     sub.setsockopt_string(zmq.SUBSCRIBE, "status")
#     sub.setsockopt_string(zmq.SUBSCRIBE, "heartbeat")

#     print(f"[STATUS] Connected to {args.pub}, subscribed to topics 'status' and 'heartbeat'")
#     try:
#         while True:
#             topic, payload = sub.recv_multipart()
#             topic = topic.decode("utf-8")
#             msg = json.loads(payload.decode("utf-8"))

#             if topic == "heartbeat":
#                 print("[STATUS] <heartbeat>", msg)
#                 continue

#             ts = msg.get("ts", int(time.time()*1000))
#             data = msg.get("data", {})
#             flags = data.get("flags", {})
#             print(f"[STATUS] ts={ts} uptime={data.get('uptime_seconds')}s "
#                   f"state={data.get('system_state')} wakeup={data.get('wakeup_time')}")
#             print(f"  packets: lora={data.get('packet_count_lora')} 433={data.get('packet_count_433')}")
#             print(f"  online: lora={flags.get('lora_online')} 433={flags.get('radio433_online')} "
#                   f"baro={flags.get('barometer_online')} current={flags.get('current_online')} "
#                   f"pi_connected={flags.get('pi_connected')}")
#             if "free_heap" in data:
#                 print(f"  heap={data['free_heap']} chip_rev={data.get('chip_revision')}")
#             print("-"*60)
#     except KeyboardInterrupt:
#         print("\n[STATUS] Exiting...")
#     finally:
#         sub.close(0)

# if __name__ == "__main__":
#     main()

### ---------------- FULL GUI VERSION BELOW ---------------- ###

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI System Status Listener → GUI Pusher
---------------------------------------
Subscribes to communicator.py PUB bus for:
  - "status" → SystemStatus_t

Attempts to POST the full system snapshot to /api/status/push.
Always mirrors a concise, human-readable status line to /api/logs/push.

Environment:
  TIMONE_PUB     (default tcp://127.0.0.1:5556)
  TIMONE_GUI_URL (default http://127.0.0.1:5000)

Payload (expects keys like):
  system_state, wakeup_time, lora_online, radio433_online,
  barometer_online, current_sensor_online, pi_connected,
  uptime_seconds, packet_count_lora, packet_count_433
"""
import os, json, time, argparse, urllib.request
from typing import Dict, Any
import zmq

SERVER_URL    = os.getenv("TIMONE_GUI_URL", "http://127.0.0.1:5000")
STATUS_ENDPOINT = f"{SERVER_URL}/api/status/push"  # optional; if not yet implemented, we silently ignore failures
LOG_ENDPOINT     = f"{SERVER_URL}/api/logs/push"

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

    push_log(f"[Status] Subscribed to {args.pub} topic: status")

    try:
        while True:
            topic_b, payload_b = sub.recv_multipart()
            try:
                msg = json.loads(payload_b.decode("utf-8"))
            except Exception:
                msg = {}

            data = msg.get("data", msg)  # accept {"data":{...}} or flat
            snap = normalize_status(data)

            # Try send to a proper status endpoint (safe to no-op if not present yet)
            push_status(snap)

            # Always mirror a readable line to Logs so operators can see heartbeat
            push_log(status_line(snap))

    except KeyboardInterrupt:
        push_log("[Status] Exiting")
    finally:
        sub.close(0)

if __name__ == "__main__":
    main()
