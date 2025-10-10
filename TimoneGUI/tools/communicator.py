#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Timone Ground Station - Communicator Daemon
-------------------------------------------

Bridges the embedded firmware (ESP32) and the Flask GUI ecosystem via a robust,
always-on Python service designed for Raspberry Pi 5.

Features
- Binary framed protocol: 0x7E HELLO, PERIPHERAL_ID, LEN(<=64), PAYLOAD, 0x7F GOODBYE
- Decoders for compact "Wire*" payloads from the firmware (versioned, packed)
- ZeroMQ PUB bus to 4 GUI-forwarder scripts (topics: lora915, radio433, barometer, current, status, raw)
- ZeroMQ REP command server for settings/system requests (single client)
- Automatic serial (re)connect, timeouts, jitter backoff, health logging
- Clean shutdown (SIGINT/SIGTERM)

Protocol & Structs reference:
- IDs, commands, max sizes and framing bytes per config.h :contentReference[oaicite:4]{index=4}
- Packed "Wire" structs layout and sizes per config.h (LoRa/433/Barometer/Current/Status) :contentReference[oaicite:5]{index=5}
- ESP32 side sends responses under PERIPHERAL_ID_SYSTEM after CMD_GET_* handling :contentReference[oaicite:6]{index=6}

Author: TimoneGUI
"""

import os
import sys
import time
import json
import struct
import signal
import threading
import logging
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any

try:
    import serial  # pyserial
    from serial.tools import list_ports
except Exception as e:
    print("ERROR: pyserial is required. pip install pyserial")
    raise

try:
    import zmq  # pyzmq
except Exception as e:
    print("ERROR: pyzmq is required. pip install pyzmq")
    raise

# ----------------------------
# Protocol constants (host copy)
# ----------------------------
HELLO_BYTE   = 0x7E  # framing start  :contentReference[oaicite:7]{index=7}
GOODBYE_BYTE = 0x7F  # framing end    :contentReference[oaicite:8]{index=8}

# Peripheral/Origin IDs            :contentReference[oaicite:9]{index=9}
PERIPHERAL_ID_SYSTEM     = 0x00
PERIPHERAL_ID_LORA_915   = 0x01
PERIPHERAL_ID_RADIO_433  = 0x02
PERIPHERAL_ID_BAROMETER  = 0x03
PERIPHERAL_ID_CURRENT    = 0x04
PERIPHERAL_EXTERNAL_1    = 0x10
PERIPHERAL_EXTERNAL_2    = 0x11
PERIPHERAL_EXTERNAL_3    = 0x12

# System/Request commands          :contentReference[oaicite:10]{index=10}
CMD_GET_LORA_DATA      = 0x01
CMD_GET_433_DATA       = 0x02
CMD_GET_BAROMETER_DATA = 0x03
CMD_GET_CURRENT_DATA   = 0x04
CMD_GET_ALL_DATA       = 0x05
CMD_GET_STATUS         = 0x06

MAX_WIRE_PAYLOAD = 255  # 1-byte length on wire (device uses <=64 for sensor packets in your spec)

# ----------------------------
# ZMQ endpoints (configurable)
# ----------------------------
PUB_ENDPOINT = os.getenv("TIMONE_PUB", "tcp://127.0.0.1:5556")
CMD_ENDPOINT = os.getenv("TIMONE_CMD", "tcp://127.0.0.1:5557")

# ----------------------------
# Serial config (auto-detectable)
# ----------------------------
DEFAULT_SERIAL_PORT = os.getenv("TIMONE_SERIAL", "")
DEFAULT_BAUD = int(os.getenv("TIMONE_BAUD", "115200"))
SERIAL_TIMEOUT_S = 0.2           # non-blocking-ish read
REPLY_TIMEOUT_S  = 1.2           # must beat device PI_COMM_TIMEOUT~1000ms :contentReference[oaicite:11]{index=11}
CONNECT_BACKOFFS = [0.5, 1, 2, 3, 5]

# ----------------------------
# Logging
# ----------------------------
logging.basicConfig(
    level=os.getenv("TIMONE_LOGLEVEL", "INFO"),
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(threadName)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("communicator")


# =============================================================================
# Wire decoders (based on config.h "Wire*" structs)
# =============================================================================
# All are little-endian; we version-gate the decode where applicable.

def _to_hex(data: bytes) -> str:
    return data.hex()

def decode_wire_payload(peripheral_hint: Optional[int], payload: bytes) -> Dict[str, Any]:
    """
    Try to decode a binary payload into a structured dict using the "Wire*" layouts.

    We prefer length+version heuristics because the firmware often replies using
    PERIPHERAL_ID_SYSTEM for GET_* commands and packs different 'Wire*' buffers. :contentReference[oaicite:12]{index=12}

    Returns a dict with:
        {"decoded": True/False, "type": "wire_{lora|433|baro|current|status}|raw",
         "data": {...} or {"payload_hex": "..."} }
    """
    n = len(payload)
    if n == 0:
        return {"decoded": False, "type": "raw", "data": {"payload_hex": ""}}

    version = payload[0]

    # WireStatus_t size = 20 bytes (v1) :contentReference[oaicite:13]{index=13}
    if n == 20 and version == 1:
        # <B I B B H H I I B = 1+4+1+1+2+2+4+4+1 = 20
        # fields: version, uptime_seconds, system_state, flags, packet_count_lora, packet_count_433,
        #         wakeup_time, free_heap, chip_revision
        try:
            tup = struct.unpack("<B I B B H H I I B", payload)
            _, uptime_s, system_state, flags, pc_lora, pc_433, wakeup_time, free_heap, chip_rev = tup
            return {
                "decoded": True,
                "type": "wire_status",
                "data": {
                    "uptime_seconds": uptime_s,
                    "system_state": system_state,
                    "flags": {
                        "lora_online": bool(flags & 0x01),
                        "radio433_online": bool(flags & 0x02),
                        "barometer_online": bool(flags & 0x04),
                        "current_online": bool(flags & 0x08),
                        "pi_connected": bool(flags & 0x10),
                    },
                    "packet_count_lora": pc_lora,
                    "packet_count_433": pc_433,
                    "wakeup_time": wakeup_time,
                    "free_heap": free_heap,
                    "chip_revision": chip_rev,
                },
            }
        except struct.error:
            pass

    # WireBarometer_t size = 17 bytes (v1) :contentReference[oaicite:14]{index=14}
    if n == 17 and version == 1:
        try:
            _, ts_ms, p_hpa, t_c, alt_m = struct.unpack("<B I f f f", payload)
            return {
                "decoded": True,
                "type": "wire_barometer",
                "data": {
                    "timestamp_ms": ts_ms,
                    "pressure_hpa": round(p_hpa, 3),
                    "temperature_c": round(t_c, 3),
                    "altitude_m": round(alt_m, 3),
                },
            }
        except struct.error:
            pass

    # WireCurrent_t size = 19 bytes (v1) :contentReference[oaicite:15]{index=15}
    if n == 19 and version == 1:
        try:
            _, ts_ms, cur_a, volt_v, pow_w, raw_adc = struct.unpack("<B I f f f h", payload)
            return {
                "decoded": True,
                "type": "wire_current",
                "data": {
                    "timestamp_ms": ts_ms,
                    "current_a": round(cur_a, 4),
                    "voltage_v": round(volt_v, 4),
                    "power_w": round(pow_w, 4),
                    "raw_adc": raw_adc,
                },
            }
        except struct.error:
            pass

    # WireLoRa_t size = 74 bytes (v1) and Wire433_t size = 70 bytes (v1)  :contentReference[oaicite:16]{index=16}
    if n in (74, 70) and version == 1:
        try:
            if n == 74:
                # <B H h f B 64s
                (ver, pkt_count, rssi_dbm, snr_db, latest_len, latest_data) = struct.unpack("<B H h f B 64s", payload)
                latest = latest_data[:latest_len]
                return {
                    "decoded": True,
                    "type": "wire_lora",
                    "data": {
                        "packet_count": pkt_count,
                        "rssi_dbm": rssi_dbm,
                        "snr_db": round(snr_db, 2),
                        "latest_len": latest_len,
                        "latest_hex": latest.hex(),
                    },
                }
            else:
                # 433: <B H h B 64s
                (ver, pkt_count, rssi_dbm, latest_len, latest_data) = struct.unpack("<B H h B 64s", payload)
                latest = latest_data[:latest_len]
                return {
                    "decoded": True,
                    "type": "wire_433",
                    "data": {
                        "packet_count": pkt_count,
                        "rssi_dbm": rssi_dbm,
                        "latest_len": latest_len,
                        "latest_hex": latest.hex(),
                    },
                }
        except struct.error:
            pass

    # Fallback: publish as hex with hint
    return {
        "decoded": False,
        "type": "raw",
        "data": {
            "peripheral_hint": peripheral_hint,
            "payload_hex": _to_hex(payload),
            "len": n,
            "version": version,
        },
    }


# =============================================================================
# Serial framing reader/writer
# =============================================================================

@dataclass
class Frame:
    peripheral_id: int
    payload: bytes

class SerialFramer:
    """Byte-stream → framed messages (and vice-versa)."""

    def __init__(self, ser: serial.Serial):
        self.ser = ser

    def write_frame(self, peripheral_id: int, payload: bytes) -> None:
        if len(payload) > MAX_WIRE_PAYLOAD:
            raise ValueError("Payload too long for 1-byte LENGTH")
        frame = bytes([HELLO_BYTE, peripheral_id, len(payload)]) + payload + bytes([GOODBYE_BYTE])
        self.ser.write(frame)
        self.ser.flush()

    def read_frame_blocking(self, timeout_s: float) -> Optional[Frame]:
        """
        Blocking read of a single frame with a total timeout.
        Returns None on timeout.
        """
        deadline = time.monotonic() + timeout_s
        # sync to HELLO
        while time.monotonic() < deadline:
            b = self.ser.read(1)
            if not b:
                continue
            if b[0] == HELLO_BYTE:
                break
        else:
            return None

        # read ID, LEN
        hdr = self._read_exact(2, deadline)
        if hdr is None:
            return None
        peripheral_id, length = hdr[0], hdr[1]

        if length > MAX_WIRE_PAYLOAD:
            # Drain until GOODBYE (best effort)
            self._drain_to_goodbye(deadline)
            return None

        payload = self._read_exact(length, deadline)
        if payload is None:
            return None

        tail = self._read_exact(1, deadline)
        if tail is None or tail[0] != GOODBYE_BYTE:
            return None

        return Frame(peripheral_id, payload)

    def _read_exact(self, n: int, deadline: float) -> Optional[bytes]:
        chunks = bytearray()
        while len(chunks) < n and time.monotonic() < deadline:
            need = n - len(chunks)
            data = self.ser.read(need)
            if data:
                chunks.extend(data)
        return bytes(chunks) if len(chunks) == n else None

    def _drain_to_goodbye(self, deadline: float):
        while time.monotonic() < deadline:
            b = self.ser.read(1)
            if not b:
                continue
            if b[0] == GOODBYE_BYTE:
                return


# =============================================================================
# Communicator main
# =============================================================================

class Communicator:
    """
    Manages:
      - Serial link (auto-reconnect)
      - Frame RX → decode → PUB fanout
      - Command REP socket (JSON in/out) → frame TX + reply RX
    """

    def __init__(self, port: str, baud: int, pub_ep: str, cmd_ep: str):
        self.port = port
        self.baud = baud
        self.pub_ep = pub_ep
        self.cmd_ep = cmd_ep

        self.ctx = zmq.Context.instance()
        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.bind(self.pub_ep)

        self.rep = self.ctx.socket(zmq.REP)
        self.rep.bind(self.cmd_ep)

        self._stop = threading.Event()
        self._ser_lock = threading.Lock()
        self._ser: Optional[serial.Serial] = None
        self._framer: Optional[SerialFramer] = None

        self.rx_thread = threading.Thread(target=self._rx_loop, name="RX", daemon=True)
        self.cmd_thread = threading.Thread(target=self._cmd_loop, name="CMD", daemon=True)

    # ---------- lifecycle ----------

    def start(self):
        log.info("Starting Communicator… PUB=%s REP=%s", self.pub_ep, self.cmd_ep)
        self.rx_thread.start()
        self.cmd_thread.start()

    def stop(self):
        self._stop.set()
        try:
            self.rep.close(0)
            self.pub.close(0)
            self.ctx.term()
        except Exception:
            pass
        with self._ser_lock:
            if self._ser and self._ser.is_open:
                try:
                    self._ser.close()
                except Exception:
                    pass

    # ---------- serial connect/reconnect ----------

    def _connect_serial(self) -> None:
        """
        Attempt to open serial. If port is blank, try to auto-detect ESP32/USB serial.
        """
        port = self.port
        if not port:
            port = self._autodetect_port()
        if not port:
            raise RuntimeError("No serial port found")

        ser = serial.Serial(
            port=port,
            baudrate=self.baud,
            timeout=SERIAL_TIMEOUT_S,
            write_timeout=SERIAL_TIMEOUT_S,
        )
        self._ser = ser
        self._framer = SerialFramer(ser)
        log.info("Serial connected: %s @ %d", port, self.baud)

    def _autodetect_port(self) -> Optional[str]:
        candidates = []
        for p in list_ports.comports():
            name = (p.device or "").lower()
            desc = (p.description or "").lower()
            if "usb" in name or "usb" in desc or "cp210" in desc or "ch340" in desc or "esp" in desc:
                candidates.append(p.device)
        return candidates[0] if candidates else None

    # ---------- RX loop ----------

    def _rx_loop(self):
        backoff_idx = 0
        last_ok = time.monotonic()
        while not self._stop.is_set():
            try:
                with self._ser_lock:
                    if self._ser is None or not self._ser.is_open:
                        self._connect_serial()
                        backoff_idx = 0

                # Read frames forever
                frame = self._framer.read_frame_blocking(timeout_s=1.0)
                if frame is None:
                    # timeout—publish heartbeat occasionally
                    if time.monotonic() - last_ok > 2.0:
                        self._publish("heartbeat", {"ts": int(time.time())})
                        last_ok = time.monotonic()
                    continue

                last_ok = time.monotonic()

                # Try to decode using Wire* heuristics
                decoded = decode_wire_payload(frame.peripheral_id, frame.payload)

                # Route to topics (when the device replies under SYSTEM, we infer type via decoder)
                topic = "raw"
                t = decoded.get("type")
                if frame.peripheral_id == PERIPHERAL_ID_LORA_915 or t == "wire_lora":
                    topic = "lora915"
                elif frame.peripheral_id == PERIPHERAL_ID_RADIO_433 or t == "wire_433":
                    topic = "radio433"
                elif frame.peripheral_id == PERIPHERAL_ID_BAROMETER or t == "wire_barometer":
                    topic = "barometer"
                elif frame.peripheral_id == PERIPHERAL_ID_CURRENT or t == "wire_current":
                    topic = "current"
                elif t == "wire_status":
                    topic = "status"

                msg = {
                    "ts": int(time.time() * 1000),
                    "peripheral_id": frame.peripheral_id,
                    "decoded": decoded["decoded"],
                    "type": decoded["type"],
                    "data": decoded["data"],
                }
                self._publish(topic, msg)

            except Exception as e:
                log.warning("RX loop error: %s", e, exc_info=True)
                # Hard reset serial and backoff
                with self._ser_lock:
                    try:
                        if self._ser:
                            self._ser.close()
                    except Exception:
                        pass
                    self._ser = None
                    self._framer = None
                delay = CONNECT_BACKOFFS[min(backoff_idx, len(CONNECT_BACKOFFS)-1)]
                backoff_idx += 1
                time.sleep(delay)

    # ---------- CMD loop ----------

    def _cmd_loop(self):
        """
        REP server: receives a JSON command and proxies to the device.

        Request schema:
            {
              "action": "GET_STATUS" | "GET_LORA" | "GET_433" | "GET_BAROMETER" | "GET_CURRENT" | "GET_ALL" | "RAW",
              "peripheral_id": 0,      # optional for RAW
              "payload_hex": "A1B2...",# required for RAW (hex payload)
            }

        Reply:
            {"ok": true, "reply": {...same shape as RX publish...}}  or {"ok": false, "error": "..."}
        """
        while not self._stop.is_set():
            try:
                req = self.rep.recv(flags=0)
            except zmq.ZMQError:
                if self._stop.is_set():
                    break
                continue

            try:
                cmd = json.loads(req.decode("utf-8"))
                action = (cmd.get("action") or "").upper()

                # Map actions to command bytes (sent under PERIPHERAL_ID_SYSTEM) :contentReference[oaicite:17]{index=17}
                if action in ("GET_STATUS", "GET_LORA", "GET_433", "GET_BAROMETER", "GET_CURRENT", "GET_ALL"):
                    cmdb = {
                        "GET_STATUS":    CMD_GET_STATUS,
                        "GET_LORA":      CMD_GET_LORA_DATA,
                        "GET_433":       CMD_GET_433_DATA,
                        "GET_BAROMETER": CMD_GET_BAROMETER_DATA,
                        "GET_CURRENT":   CMD_GET_CURRENT_DATA,
                        "GET_ALL":       CMD_GET_ALL_DATA,
                    }[action]
                    reply = self._roundtrip(PERIPHERAL_ID_SYSTEM, bytes([cmdb]))
                elif action == "RAW":
                    pid = int(cmd.get("peripheral_id", PERIPHERAL_ID_SYSTEM))
                    payload_hex = cmd.get("payload_hex", "")
                    payload = bytes.fromhex(payload_hex) if payload_hex else b""
                    reply = self._roundtrip(pid, payload)
                else:
                    raise ValueError(f"Unsupported action: {action}")

                self.rep.send_json({"ok": True, "reply": reply})
            except Exception as e:
                self.rep.send_json({"ok": False, "error": str(e)})

    def _roundtrip(self, peripheral_id: int, payload: bytes) -> Dict[str, Any]:
        """
        Send one framed request and wait for a single framed reply.
        """
        with self._ser_lock:
            if not self._framer:
                raise RuntimeError("Serial not connected")
            self._framer.write_frame(peripheral_id, payload)

        # Wait for reply
        t0 = time.monotonic()
        while time.monotonic() - t0 < REPLY_TIMEOUT_S:
            with self._ser_lock:
                if not self._framer:
                    break
                frame = self._framer.read_frame_blocking(timeout_s=0.2)
            if frame is None:
                continue
            decoded = decode_wire_payload(frame.peripheral_id, frame.payload)
            topic = decoded.get("type", "raw")
            return {
                "peripheral_id": frame.peripheral_id,
                "type": topic,
                "decoded": decoded["decoded"],
                "data": decoded["data"],
            }
        raise TimeoutError("No reply from device")

    # ---------- PUB helper ----------

    def _publish(self, topic: str, payload: Dict[str, Any]) -> None:
        try:
            self.pub.send_multipart([topic.encode("utf-8"), json.dumps(payload).encode("utf-8")], flags=zmq.NOBLOCK)
        except zmq.Again:
            pass


# =============================================================================
# Entrypoint
# =============================================================================

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Timone Communicator Daemon")
    ap.add_argument("--port", default=DEFAULT_SERIAL_PORT, help="Serial port (auto-detect if empty)")
    ap.add_argument("--baud", default=DEFAULT_BAUD, type=int, help="Serial baudrate")
    ap.add_argument("--pub",  default=PUB_ENDPOINT, help="ZMQ PUB endpoint (bind)")
    ap.add_argument("--cmd",  default=CMD_ENDPOINT, help="ZMQ REP endpoint (bind)")
    args = ap.parse_args()

    comm = Communicator(args.port, args.baud, args.pub, args.cmd)

    stop = False
    def _stop_handler(signum, frame):
        nonlocal stop
        if not stop:
            stop = True
            log.info("Shutting down…")
            comm.stop()
        else:
            os._exit(1)

    signal.signal(signal.SIGINT, _stop_handler)
    signal.signal(signal.SIGTERM, _stop_handler)

    comm.start()
    # Sleep until stopped
    while not stop:
        time.sleep(0.5)

if __name__ == "__main__":
    main()
