#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI Peripherals Listener
------------------------
Subscribes to barometer/current (and optionally raw for future externals) and prints updates.
Run:
  python3 gui_peripherals.py --pub tcp://127.0.0.1:5556
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
    ap.add_argument("--include-raw", action="store_true",
                    help="Also subscribe to 'raw' for unknown/external peripherals")
    args = ap.parse_args()

    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.connect(args.pub)
    sub.setsockopt_string(zmq.SUBSCRIBE, "barometer")
    sub.setsockopt_string(zmq.SUBSCRIBE, "current")
    if args.include_raw:
        sub.setsockopt_string(zmq.SUBSCRIBE, "raw")

    print(f"[PERIPH] Connected to {args.pub}, topics: barometer, current"
          + (", raw" if args.include_raw else ""))
    try:
        while True:
            topic, payload = sub.recv_multipart()
            topic = topic.decode("utf-8")
            msg = json.loads(payload.decode("utf-8"))
            ts = msg.get("ts", int(time.time()*1000))
            data = msg.get("data", {})

            if topic == "barometer":
                print(f"[BARO] ts={ts} P={data.get('pressure_hpa')} hPa  T={data.get('temperature_c')} °C "
                      f"Alt={data.get('altitude_m')} m")
            elif topic == "current":
                print(f"[CURR] ts={ts} I={data.get('current_a')} A  V={data.get('voltage_v')} V  "
                      f"P={data.get('power_w')} W  raw_adc={data.get('raw_adc')}")
            else:
                # raw / unknown external
                print(f"[RAW] ts={ts} pid={msg.get('peripheral_id')} decoded={msg.get('decoded')} "
                      f"type={msg.get('type')} len={data.get('len')} hex={data.get('payload_hex')}")
            print("-"*60)
    except KeyboardInterrupt:
        print("\n[PERIPH] Exiting...")
    finally:
        sub.close(0)

if __name__ == "__main__":
    main()
