#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI System Status Listener
--------------------------
Subscribes to the communicator PUB bus and prints system/status frames.
Run:
  python3 gui_status.py --pub tcp://127.0.0.1:5556
"""
import os
import sys
import json
import time
import argparse
import zmq

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pub", default=os.getenv("TIMONE_PUB", "tcp://127.0.0.1:5556"),
                    help="PUB endpoint exposed by communicator.py")
    args = ap.parse_args()

    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.connect(args.pub)
    # Status + heartbeat are useful here
    sub.setsockopt_string(zmq.SUBSCRIBE, "status")
    sub.setsockopt_string(zmq.SUBSCRIBE, "heartbeat")

    print(f"[STATUS] Connected to {args.pub}, subscribed to topics 'status' and 'heartbeat'")
    try:
        while True:
            topic, payload = sub.recv_multipart()
            topic = topic.decode("utf-8")
            msg = json.loads(payload.decode("utf-8"))

            if topic == "heartbeat":
                print("[STATUS] <heartbeat>", msg)
                continue

            ts = msg.get("ts", int(time.time()*1000))
            data = msg.get("data", {})
            flags = data.get("flags", {})
            print(f"[STATUS] ts={ts} uptime={data.get('uptime_seconds')}s "
                  f"state={data.get('system_state')} wakeup={data.get('wakeup_time')}")
            print(f"  packets: lora={data.get('packet_count_lora')} 433={data.get('packet_count_433')}")
            print(f"  online: lora={flags.get('lora_online')} 433={flags.get('radio433_online')} "
                  f"baro={flags.get('barometer_online')} current={flags.get('current_online')} "
                  f"pi_connected={flags.get('pi_connected')}")
            if "free_heap" in data:
                print(f"  heap={data['free_heap']} chip_rev={data.get('chip_revision')}")
            print("-"*60)
    except KeyboardInterrupt:
        print("\n[STATUS] Exiting...")
    finally:
        sub.close(0)

if __name__ == "__main__":
    main()
