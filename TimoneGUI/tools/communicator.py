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
- Autonomous polling initialization on boot
- Clean shutdown (SIGINT/SIGTERM)

Protocol & Structs reference:
- IDs, commands, max sizes and framing bytes per config.h
- Packed "Wire" structs layout and sizes per config.h (LoRa/433/Barometer/Current/Status)
- ESP32 uses modular command architecture with peripheral-specific addressing

Author: TimoneGUI
Version: 2.0 - Updated for embedded protocol synchronization
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
# Protocol constants (host copy) - UPDATED TO MATCH EMBEDDED PROTOCOL
# ----------------------------
HELLO_BYTE   = 0x7E  # framing start (Pi → ESP32)
RESPONSE_BYTE = 0x7D  # framing start (ESP32 → Pi, though we also accept HELLO_BYTE for compatibility)
GOODBYE_BYTE = 0x7F  # framing end

# Peripheral/Origin IDs (config.h peripheral definitions)
PERIPHERAL_ID_SYSTEM     = 0x00  # ESP32 system control
PERIPHERAL_ID_LORA_915   = 0x01  # 915MHz LoRa module
PERIPHERAL_ID_RADIO_433  = 0x02  # 433MHz LoRa module (same chip, different freq)
PERIPHERAL_ID_BAROMETER  = 0x03  # MS5607 barometer
PERIPHERAL_ID_CURRENT    = 0x04  # Current/voltage sensor
PERIPHERAL_ID_ALL        = 0xFF  # Special: targets all sensor peripherals

# External/future peripherals
PERIPHERAL_EXTERNAL_1    = 0x10
PERIPHERAL_EXTERNAL_2    = 0x11
PERIPHERAL_EXTERNAL_3    = 0x12

# Generic sensor commands (0x00-0x0F) - work for ALL sensor peripherals
# These are sent TO the specific peripheral, not to SYSTEM
CMD_GET_ALL         = 0x00  # Get all available data from this peripheral (one-time)
CMD_GET_STATUS      = 0x01  # Get status/health of this peripheral
CMD_SET_POLL_RATE   = 0x02  # Set autonomous polling rate (payload: 2 bytes interval_ms)
CMD_STOP_POLL       = 0x03  # Stop autonomous polling (no payload)

# System commands (0x20-0x2F) - only for PERIPHERAL_ID_SYSTEM
CMD_SYSTEM_STATUS   = 0x20  # Get full WireStatus_t (20 bytes)
CMD_SYSTEM_WAKEUP   = 0x21  # Wake up system from low-power state
CMD_SYSTEM_SLEEP    = 0x22  # Put system into low-power state
CMD_SYSTEM_RESET    = 0x23  # Reset entire ESP32

MAX_WIRE_PAYLOAD = 255  # 1-byte length on wire

# ----------------------------
# Polling configuration
# ----------------------------
# Default polling intervals (in milliseconds) for autonomous data streaming
# These are sent to the ESP32 on boot to initialize continuous data flow
DEFAULT_POLL_RATES = {
    PERIPHERAL_ID_LORA_915:  1000,  # 1 second - radio telemetry
    PERIPHERAL_ID_RADIO_433: 1000,  # 1 second - radio telemetry
    PERIPHERAL_ID_BAROMETER: 2000,  # 2 seconds - environmental data
    PERIPHERAL_ID_CURRENT:   2000,  # 2 seconds - power monitoring
}

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
REPLY_TIMEOUT_S  = 1.2           # must beat device PI_COMM_TIMEOUT~1000ms
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
    """Convert bytes to hex string."""
    return data.hex()

def decode_wire_payload(peripheral_hint: Optional[int], payload: bytes) -> Dict[str, Any]:
    """
    Try to decode a binary payload into a structured dict using the "Wire*" layouts.

    We use length+version heuristics because the firmware may reply with different
    Wire* struct types depending on which peripheral was queried.

    Args:
        peripheral_hint: The peripheral_id from the frame (may help with ambiguous cases)
        payload: Raw bytes from the ESP32

    Returns:
        A dict with:
            {"decoded": True/False, 
             "type": "wire_{lora|433|baro|current|status}|raw",
             "data": {...} or {"payload_hex": "..."} }
    """
    n = len(payload)
    if n == 0:
        return {"decoded": False, "type": "raw", "data": {"payload_hex": ""}}

    version = payload[0]

    # WireStatus_t size = 20 bytes (v1)
    # Format: <B I B B H H I I B
    # Fields: version, uptime_seconds, system_state, flags, packet_count_lora, 
    #         packet_count_433, wakeup_time, free_heap, chip_revision
    if n == 20 and version == 1:
        try:
            tup = struct.unpack("<B I B B H H I I B", payload)
            _, uptime_s, system_state, flags, pc_lora, pc_433, wakeup_time, free_heap, chip_rev = tup
            return {
                "decoded": True,
                "type": "wire_status",
                "data": {
                    "uptime_seconds": uptime_s,
                    "system_state": system_state,
                    "lora_online": bool(flags & 0x01),
                    "radio433_online": bool(flags & 0x02),
                    "barometer_online": bool(flags & 0x04),
                    "current_sensor_online": bool(flags & 0x08),
                    "pi_connected": bool(flags & 0x10),
                    "packet_count_lora": pc_lora,
                    "packet_count_433": pc_433,
                    "wakeup_time": wakeup_time,
                    "free_heap": free_heap,
                    "chip_revision": chip_rev,
                },
            }
        except struct.error:
            pass

    # WireBarometer_t size = 17 bytes (v1)
    # Format: <B I f f f
    # Fields: version, timestamp_ms, pressure_hpa, temperature_c, altitude_m
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

    # WireCurrent_t size = 19 bytes (v1)
    # Format: <B I f f f h
    # Fields: version, timestamp_ms, current_a, voltage_v, power_w, raw_adc
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

    # WireLoRa_t size = 74 bytes (v1)
    # Format: <B H h f B 64s
    # Fields: version, packet_count, rssi_dbm, snr_db, latest_len, latest_data[64]
    # Wire433_t is identical structure (same LoRa chip, different frequency)
    if n == 74 and version == 1:
        try:
            (ver, pkt_count, rssi_dbm, snr_db, latest_len, latest_data) = struct.unpack("<B H h f B 64s", payload)
            latest = latest_data[:latest_len]
            
            # Try to decode as ASCII for logging convenience
            try:
                latest_ascii = latest.decode("utf-8", errors="ignore").strip()
            except Exception:
                latest_ascii = ""

            # Determine if this is 915 or 433 based on peripheral_hint
            wire_type = "wire_lora" if peripheral_hint == PERIPHERAL_ID_LORA_915 else "wire_433"
            
            return {
                "decoded": True,
                "type": wire_type,
                "data": {
                    "packet_count": pkt_count,
                    "rssi_dbm": rssi_dbm,
                    "snr_db": round(snr_db, 2),
                    "latest_len": latest_len,
                    "latest_hex": latest.hex(),
                    "latest_ascii": latest_ascii,  # Convenience field for text payloads
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
    """Represents a single binary frame from the ESP32."""
    peripheral_id: int
    payload: bytes

class SerialFramer:
    """
    Byte-stream → framed messages (and vice-versa).
    
    Handles the binary protocol:
        [HELLO_BYTE] [PERIPHERAL_ID] [LENGTH] [PAYLOAD...] [GOODBYE_BYTE]
    """

    def __init__(self, ser: serial.Serial):
        """
        Initialize framer with a serial port.
        
        Args:
            ser: An open pyserial Serial object
        """
        self.ser = ser

    def write_frame(self, peripheral_id: int, payload: bytes) -> None:
        """
        Write a framed message to the serial port.
        
        Args:
            peripheral_id: Target peripheral (0x00-0xFF)
            payload: Command + data bytes (max 255 bytes)
            
        Raises:
            ValueError: If payload is too long for 1-byte LENGTH field
        """
        if len(payload) > MAX_WIRE_PAYLOAD:
            raise ValueError("Payload too long for 1-byte LENGTH")
        frame = bytes([HELLO_BYTE, peripheral_id, len(payload)]) + payload + bytes([GOODBYE_BYTE])
        self.ser.write(frame)
        self.ser.flush()

    def read_frame_blocking(self, timeout_s: float) -> Optional[Frame]:
        """
        Blocking read of a single frame with a total timeout.
        
        Synchronizes to HELLO_BYTE, reads header, payload, and validates GOODBYE_BYTE.
        
        Args:
            timeout_s: Total time to wait for a complete frame
            
        Returns:
            Frame object if successful, None on timeout or invalid frame
        """
        deadline = time.monotonic() + timeout_s
        
        # Sync to HELLO (also accept RESPONSE_BYTE for ESP32→Pi messages)
        while time.monotonic() < deadline:
            b = self.ser.read(1)
            if not b:
                continue
            if b[0] in (HELLO_BYTE, RESPONSE_BYTE):
                break
        else:
            return None

        # Read peripheral ID and length
        hdr = self._read_exact(2, deadline)
        if hdr is None:
            return None
        peripheral_id, length = hdr[0], hdr[1]

        if length > MAX_WIRE_PAYLOAD:
            # Malformed frame - drain until GOODBYE (best effort)
            self._drain_to_goodbye(deadline)
            return None

        # Read payload
        payload = self._read_exact(length, deadline)
        if payload is None:
            return None

        # Validate GOODBYE byte
        tail = self._read_exact(1, deadline)
        if tail is None or tail[0] != GOODBYE_BYTE:
            return None

        return Frame(peripheral_id, payload)

    def _read_exact(self, n: int, deadline: float) -> Optional[bytes]:
        """
        Read exactly n bytes before deadline.
        
        Args:
            n: Number of bytes to read
            deadline: Absolute time (from monotonic clock) to stop trying
            
        Returns:
            bytes of length n, or None if timeout
        """
        chunks = bytearray()
        while len(chunks) < n and time.monotonic() < deadline:
            need = n - len(chunks)
            data = self.ser.read(need)
            if data:
                chunks.extend(data)
        return bytes(chunks) if len(chunks) == n else None

    def _drain_to_goodbye(self, deadline: float):
        """
        Best-effort drain until GOODBYE_BYTE or deadline.
        
        Used to recover from malformed frames.
        """
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
      - Autonomous polling initialization on boot
    """

    def __init__(self, port: str, baud: int, pub_ep: str, cmd_ep: str):
        """
        Initialize the communicator.
        
        Args:
            port: Serial port path (empty string for auto-detect)
            baud: Serial baud rate
            pub_ep: ZeroMQ PUB endpoint (tcp://...)
            cmd_ep: ZeroMQ REP endpoint (tcp://...)
        """
        self.port = port
        self.baud = baud
        self.pub_ep = pub_ep
        self.cmd_ep = cmd_ep

        # ZeroMQ sockets
        self.ctx = zmq.Context.instance()
        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.bind(self.pub_ep)

        self.rep = self.ctx.socket(zmq.REP)
        self.rep.bind(self.cmd_ep)

        # Threading
        self._stop = threading.Event()
        self._ser_lock = threading.Lock()
        self._ser: Optional[serial.Serial] = None
        self._framer: Optional[SerialFramer] = None
        self._polling_initialized = False  # Track if we've sent initial poll rates

        self.rx_thread = threading.Thread(target=self._rx_loop, name="RX", daemon=True)
        self.cmd_thread = threading.Thread(target=self._cmd_loop, name="CMD", daemon=True)

    # ---------- lifecycle ----------

    def start(self):
        """Start the RX and CMD worker threads."""
        log.info("Starting Communicator… PUB=%s REP=%s", self.pub_ep, self.cmd_ep)
        self.rx_thread.start()
        self.cmd_thread.start()

    def stop(self):
        """Stop all threads and close connections."""
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
        
        Raises:
            RuntimeError: If no serial port found or connection fails
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
        self._polling_initialized = False  # Reset flag on new connection
        log.info("Serial connected: %s @ %d", port, self.baud)

    def _autodetect_port(self) -> Optional[str]:
        """
        Auto-detect USB serial port (ESP32, CP210x, CH340, etc).
        
        Returns:
            First matching port path, or None if not found
        """
        candidates = []
        for p in list_ports.comports():
            name = (p.device or "").lower()
            desc = (p.description or "").lower()
            if "usb" in name or "usb" in desc or "cp210" in desc or "ch340" in desc or "esp" in desc:
                candidates.append(p.device)
        return candidates[0] if candidates else None

    # ---------- polling initialization ----------

    def _initialize_polling(self) -> None:
        """
        Send CMD_SET_POLL_RATE to each peripheral to start autonomous data streaming.
        
        This is called once after serial connection is established. The ESP32 will
        then continuously send sensor data at the specified intervals via the
        autonomous polling system (checkAndSendPollingData in main.cpp).
        """
        if self._polling_initialized:
            return  # Already initialized
            
        with self._ser_lock:
            if not self._framer:
                return  # Not connected yet

        log.info("Initializing autonomous polling on ESP32...")
        
        for peripheral_id, interval_ms in DEFAULT_POLL_RATES.items():
            try:
                # Build payload: CMD_SET_POLL_RATE + 2-byte little-endian interval
                payload = struct.pack("<BH", CMD_SET_POLL_RATE, interval_ms)
                
                with self._ser_lock:
                    if not self._framer:
                        log.warning("Lost connection during polling init")
                        return
                    self._framer.write_frame(peripheral_id, payload)
                
                # Small delay between commands to avoid overwhelming the ESP32
                time.sleep(0.05)
                
                log.info("  ✓ Polling enabled: peripheral=0x%02X interval=%dms", 
                        peripheral_id, interval_ms)
                        
            except Exception as e:
                log.warning("Failed to set poll rate for peripheral 0x%02X: %s", 
                           peripheral_id, e)
        
        self._polling_initialized = True
        log.info("Autonomous polling initialization complete")

    # ---------- RX loop ----------

    def _rx_loop(self):
        """
        Continuously read frames from serial, decode them, and publish to ZMQ.
        
        Handles reconnection on errors with exponential backoff.
        Initializes polling after successful connection.
        """
        backoff_idx = 0
        last_ok = time.monotonic()
        
        while not self._stop.is_set():
            try:
                # Ensure serial is connected
                with self._ser_lock:
                    if self._ser is None or not self._ser.is_open:
                        self._connect_serial()
                        backoff_idx = 0

                # Initialize polling if this is a fresh connection
                if not self._polling_initialized:
                    time.sleep(0.5)  # Give ESP32 time to boot/stabilize
                    self._initialize_polling()

                # Read frames forever
                frame = self._framer.read_frame_blocking(timeout_s=1.0)
                if frame is None:
                    # Timeout—publish heartbeat occasionally
                    if time.monotonic() - last_ok > 2.0:
                        self._publish("heartbeat", {"ts": int(time.time())})
                        last_ok = time.monotonic()
                    continue

                last_ok = time.monotonic()

                # Try to decode using Wire* heuristics
                decoded = decode_wire_payload(frame.peripheral_id, frame.payload)

                # Route to topics based on peripheral_id or decoded type
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

                # Publish to ZMQ
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
                    self._polling_initialized = False  # Reset on disconnect
                delay = CONNECT_BACKOFFS[min(backoff_idx, len(CONNECT_BACKOFFS)-1)]
                backoff_idx += 1
                time.sleep(delay)

    # ---------- CMD loop ----------

    def _cmd_loop(self):
        """
        REP server: receives a JSON command and proxies to the device.

        Request schema:
            {
              "action": "GET_STATUS" | "GET_LORA" | "GET_433" | "GET_BAROMETER" | 
                        "GET_CURRENT" | "RAW",
              "peripheral_id": 0,      # optional/ignored for GET_* actions, required for RAW
              "payload_hex": "A1B2...",# required for RAW (hex-encoded payload)
            }

        Reply:
            {"ok": true, "reply": {...same shape as RX publish...}}  
            or 
            {"ok": false, "error": "..."}
            
        Note: Updated command routing to match embedded protocol architecture.
              Commands are now sent directly to peripherals using CMD_GET_ALL,
              rather than the old system where everything went through PERIPHERAL_ID_SYSTEM.
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

                # Map high-level actions to the new protocol
                if action == "GET_STATUS":
                    # System status uses PERIPHERAL_ID_SYSTEM + CMD_SYSTEM_STATUS
                    reply = self._roundtrip(PERIPHERAL_ID_SYSTEM, bytes([CMD_SYSTEM_STATUS]))
                    
                elif action == "GET_LORA":
                    # Get LoRa 915MHz data: send CMD_GET_ALL to PERIPHERAL_ID_LORA_915
                    reply = self._roundtrip(PERIPHERAL_ID_LORA_915, bytes([CMD_GET_ALL]))
                    
                elif action == "GET_433":
                    # Get 433MHz data: send CMD_GET_ALL to PERIPHERAL_ID_RADIO_433
                    reply = self._roundtrip(PERIPHERAL_ID_RADIO_433, bytes([CMD_GET_ALL]))
                    
                elif action == "GET_BAROMETER":
                    # Get barometer data: send CMD_GET_ALL to PERIPHERAL_ID_BAROMETER
                    reply = self._roundtrip(PERIPHERAL_ID_BAROMETER, bytes([CMD_GET_ALL]))
                    
                elif action == "GET_CURRENT":
                    # Get current sensor data: send CMD_GET_ALL to PERIPHERAL_ID_CURRENT
                    reply = self._roundtrip(PERIPHERAL_ID_CURRENT, bytes([CMD_GET_ALL]))
                    
                elif action == "RAW":
                    # Pass-through: user specifies peripheral_id and payload directly
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
        
        Args:
            peripheral_id: Target peripheral (0x00-0xFF)
            payload: Command byte(s) to send
            
        Returns:
            Decoded reply as dict with keys: peripheral_id, type, decoded, data
            
        Raises:
            RuntimeError: If serial not connected
            TimeoutError: If no reply received within REPLY_TIMEOUT_S
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
                
            # Decode and return
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
        """
        Publish a message to the ZMQ PUB socket.
        
        Args:
            topic: Topic string (e.g. "lora915", "status")
            payload: Message dict (will be JSON-encoded)
        """
        try:
            self.pub.send_multipart([topic.encode("utf-8"), json.dumps(payload).encode("utf-8")], flags=zmq.NOBLOCK)
        except zmq.Again:
            pass


# =============================================================================
# Entrypoint
# =============================================================================

def main():
    """
    Main entry point for the communicator daemon.
    
    Parses command-line arguments, sets up signal handlers, and runs the main loop.
    """
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