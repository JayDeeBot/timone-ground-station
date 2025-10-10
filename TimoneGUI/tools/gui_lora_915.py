#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI Lora915 Listener
--------------------
Subscribes to the communicator PUB bus and prints LoRa 915 telemetry.
Run:
  python3 gui_lora_915.py --pub tcp://127.0.0.1:5556
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
    sub.setsockopt_string(zmq.SUBSCRIBE, "lora915")

    print(f"[LORA915] Connected to {args.pub}, subscribed to topic 'lora915'")
    try:
        while True:
            topic, payload = sub.recv_multipart()
            msg = json.loads(payload.decode("utf-8"))
            ts = msg.get("ts", int(time.time()*1000))
            data = msg.get("data", {})
            print(f"[LORA915] ts={ts} decoded={msg.get('decoded')} type={msg.get('type')}")
            print(f"  packet_count={data.get('packet_count')} rssi_dbm={data.get('rssi_dbm')} "
                  f"snr_db={data.get('snr_db')} latest_len={data.get('latest_len')}")
            if "latest_hex" in data:
                print(f"  latest_hex={data['latest_hex']}")
            print("-"*60)
    except KeyboardInterrupt:
        print("\n[LORA915] Exiting...")
    finally:
        sub.close(0)

if __name__ == "__main__":
    main()
