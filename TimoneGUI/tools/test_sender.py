
#!/usr/bin/env python3
"""
test_sender.py - Timone Ground Station Test Tool

A Tkinter GUI to craft and send test messages to the ESP32 ground station using
the new peripheral-based protocol and display decoded responses.

NEW PROTOCOL ARCHITECTURE (Peripheral-Based):
==============================================
Message Structure:
  Pi → ESP32 (Command):  [HELLO=0x7E][PERIPHERAL_ID][LENGTH][COMMAND][data...][GOODBYE=0x7F]
  ESP32 → Pi (Response): [RESPONSE=0x7D][PERIPHERAL_ID][LENGTH][data...][GOODBYE=0x7F]

Key Changes:
  - Commands sent to SPECIFIC peripherals (not just SYSTEM)
  - Responses use RESPONSE_BYTE (0x7D) instead of HELLO_BYTE (0x7E)
  - Each peripheral handles its own commands independently
  - Generic commands (0x00-0x0F) work for ALL peripherals
  - System commands (0x20-0x2F) only for PERIPHERAL_ID=0x00

Peripheral IDs:
  0x00 - SYSTEM (ESP32 control)
  0x01 - LORA_915 (915MHz LoRa)
  0x02 - LORA_433 (433MHz LoRa/Radio)
  0x03 - BAROMETER (MS5607)
  0x04 - CURRENT (Current/voltage sensor)
  0x10-0x13 - AIM_1 to AIM_4 (future)

Generic Commands (all peripherals):
  0x00 - CMD_GET_ALL (get all data from peripheral)
  0x01 - CMD_GET_STATUS (get status/health)
  0x02 - CMD_RESET (reset peripheral)
  0x03 - CMD_CONFIGURE (configure peripheral)

System Commands (PERIPHERAL_ID=0x00 only):
  0x20 - CMD_SYSTEM_WAKEUP (wake from low-power)
  0x21 - CMD_SYSTEM_SLEEP (enter low-power)
  0x22 - CMD_SYSTEM_RESET (reset ESP32)

Features:
  - Serial port selection & connect/disconnect
  - Peripheral + Command dropdowns with NEW protocol
  - Automatic payload construction (command byte + optional data)
  - Response decoder with struct unpacking for known data types
  - Quick action buttons for common operations
  - Save/Load presets

Requirements:
  pip install pyserial

Updated: 2025-10-17 (New peripheral-based protocol)
"""

import sys
import os
import time
import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests  # For sending data to web GUI

# Guard pyserial import so the GUI can still open and show a message
try:
    import serial
    from serial.tools import list_ports
except Exception as e:
    serial = None
    list_ports = None

# ---- Protocol constants (NEW PERIPHERAL-BASED PROTOCOL) ----
import struct

HELLO_BYTE = 0x7E       # Start of Pi → ESP32 message
RESPONSE_BYTE = 0x7D    # Start of ESP32 → Pi message (different from HELLO!)
GOODBYE_BYTE = 0x7F     # End of message marker

# Peripheral IDs
PERIPHERALS = {
    "SYSTEM":      0x00,
    "LORA_915":    0x01,
    "LORA_433":    0x02,  # Also called RADIO_433
    "BAROMETER":   0x03,
    "CURRENT":     0x04,
    "AIM_1":       0x10,
    "AIM_2":       0x11,
    "AIM_3":       0x12,
    "AIM_4":       0x13,
    "ALL":         0xFF,  # NEW: Special ID for all sensors
}

# Generic Commands (work for sensor peripherals only: 0x00-0x0F)
GENERIC_COMMANDS = {
    "GET_ALL":        0x00,  # Get all available data from peripheral
    "GET_STATUS":     0x01,  # Health check ping
    "SET_POLL_RATE":  0x02,  # NEW: Set autonomous polling rate (2-byte interval_ms in payload)
    "STOP_POLL":      0x03,  # NEW: Stop autonomous polling (alias for SET_POLL_RATE with 0ms)
}

# System-only Commands (only for PERIPHERAL_ID = 0x00: 0x20-0x2F)
SYSTEM_COMMANDS = {
    "SYSTEM_STATUS":  0x20,  # Get full WireStatus_t (20 bytes)
    "SYSTEM_WAKEUP":  0x21,  # Wake up from low-power state
    "SYSTEM_SLEEP":   0x22,  # Enter low-power state
    "SYSTEM_RESET":   0x23,  # Reset entire ESP32
}

# Mapping of peripherals to their command sets
PERIPHERAL_COMMANDS = {
    "SYSTEM": SYSTEM_COMMANDS.copy(),  # System only gets system commands
    "LORA_915": GENERIC_COMMANDS.copy(),
    "LORA_433": GENERIC_COMMANDS.copy(),
    "BAROMETER": GENERIC_COMMANDS.copy(),
    "CURRENT": GENERIC_COMMANDS.copy(),
    "AIM_1": GENERIC_COMMANDS.copy(),
    "AIM_2": GENERIC_COMMANDS.copy(),
    "AIM_3": GENERIC_COMMANDS.copy(),
    "AIM_4": GENERIC_COMMANDS.copy(),
    "ALL": GENERIC_COMMANDS.copy(),  # ALL peripheral uses generic commands
}

# Data structure sizes (for validation and parsing)
SIZE_HEARTBEAT = 6   # WireHeartbeat_t
SIZE_STATUS = 20     # WireStatus_t
SIZE_LORA = 74       # WireLoRa_t
SIZE_433 = 74        # Wire433_t (same as LoRa)
SIZE_BAROMETER = 17  # WireBarometer_t
SIZE_CURRENT = 19    # WireCurrent_t

DEFAULT_BAUD = 115200
READ_TIMEOUT_S = 0.5   # serial read timeout per call (reduced from 2.0s)
RX_TOTAL_TIMEOUT_S = 1.0  # overall receive timeout for a whole frame (reduced from 3.0s)

def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)

def parse_hex_bytes(s: str) -> bytes:
    s = s.strip()
    if not s:
        return b""
    try:
        parts = s.replace(",", " ").split()
        return bytes(int(p, 16) for p in parts)
    except ValueError as e:
        raise ValueError("Payload must be hex bytes like: '01 02 0A FF'") from e

# ---- Data Structure Unpacking Functions ----
def unpack_heartbeat(data: bytes) -> dict:
    """Unpack WireHeartbeat_t (6 bytes): version(1), uptime(4), state(1)"""
    result = {'partial': len(data) != SIZE_HEARTBEAT, 'actual_length': len(data), 'expected_length': SIZE_HEARTBEAT}
    offset = 0

    # Decode what we can from available bytes
    if len(data) >= offset + 1:
        result['version'] = struct.unpack('<B', data[offset:offset+1])[0]
        offset += 1
    if len(data) >= offset + 4:
        result['uptime_seconds'] = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
    if len(data) >= offset + 1:
        result['system_state'] = struct.unpack('<B', data[offset:offset+1])[0]

    return result

def unpack_status(data: bytes) -> dict:
    """Unpack WireStatus_t (20 bytes): version(1), uptime(4), state(1), flags(1), pkt_lora(2), pkt_433(2), wakeup_time(4), heap(4), chip_rev(1)"""
    result = {'partial': len(data) != SIZE_STATUS, 'actual_length': len(data), 'expected_length': SIZE_STATUS}
    offset = 0

    # Decode what we can from available bytes
    if len(data) >= offset + 1:
        result['version'] = struct.unpack('<B', data[offset:offset+1])[0]
        offset += 1
    if len(data) >= offset + 4:
        result['uptime_seconds'] = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
    if len(data) >= offset + 1:
        result['system_state'] = struct.unpack('<B', data[offset:offset+1])[0]
        offset += 1
    if len(data) >= offset + 1:
        result['sensor_flags'] = struct.unpack('<B', data[offset:offset+1])[0]
        offset += 1
    if len(data) >= offset + 2:
        result['pkt_count_lora'] = struct.unpack('<H', data[offset:offset+2])[0]
        offset += 2
    if len(data) >= offset + 2:
        result['pkt_count_433'] = struct.unpack('<H', data[offset:offset+2])[0]
        offset += 2
    if len(data) >= offset + 4:
        result['wakeup_time'] = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
    if len(data) >= offset + 4:
        result['heap_free'] = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
    if len(data) >= offset + 1:
        result['chip_revision'] = struct.unpack('<B', data[offset:offset+1])[0]

    return result

def unpack_lora_data(data: bytes) -> dict:
    """Unpack WireLoRa_t (74 bytes): version(1), pkt_count(2), rssi(2), snr(4), len(1), data(64)"""
    result = {'partial': len(data) != SIZE_LORA, 'actual_length': len(data), 'expected_length': SIZE_LORA}
    offset = 0

    # Decode what we can from available bytes
    if len(data) >= offset + 1:
        result['version'] = struct.unpack('<B', data[offset:offset+1])[0]
        offset += 1
    if len(data) >= offset + 2:
        result['packet_count'] = struct.unpack('<H', data[offset:offset+2])[0]
        offset += 2
    if len(data) >= offset + 2:
        result['rssi'] = struct.unpack('<h', data[offset:offset+2])[0]
        offset += 2
    if len(data) >= offset + 4:
        result['snr'] = struct.unpack('<f', data[offset:offset+4])[0]
        offset += 4
    if len(data) >= offset + 1:
        result['payload_length'] = struct.unpack('<B', data[offset:offset+1])[0]
        offset += 1
    if len(data) >= offset + 64:
        payload_data = data[offset:offset+64]
        # Trim to actual payload length if we have it
        if 'payload_length' in result:
            result['payload'] = payload_data[:result['payload_length']]
        else:
            result['payload'] = payload_data

    return result

def unpack_barometer_data(data: bytes) -> dict:
    """Unpack WireBarometer_t (17 bytes): version(1), timestamp(4), pressure(4), temp(4), altitude(4)"""
    result = {'partial': len(data) != SIZE_BAROMETER, 'actual_length': len(data), 'expected_length': SIZE_BAROMETER}
    offset = 0

    # Decode what we can from available bytes
    if len(data) >= offset + 1:
        result['version'] = struct.unpack('<B', data[offset:offset+1])[0]
        offset += 1
    if len(data) >= offset + 4:
        result['timestamp'] = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
    if len(data) >= offset + 4:
        result['pressure_pa'] = struct.unpack('<f', data[offset:offset+4])[0]
        offset += 4
    if len(data) >= offset + 4:
        result['temperature_c'] = struct.unpack('<f', data[offset:offset+4])[0]
        offset += 4
    if len(data) >= offset + 4:
        result['altitude_m'] = struct.unpack('<f', data[offset:offset+4])[0]

    return result

def unpack_current_data(data: bytes) -> dict:
    """Unpack WireCurrent_t (19 bytes): version(1), timestamp(4), current(4), voltage(4), power(4), raw_adc(2)"""
    result = {'partial': len(data) != SIZE_CURRENT, 'actual_length': len(data), 'expected_length': SIZE_CURRENT}
    offset = 0

    # Decode what we can from available bytes
    if len(data) >= offset + 1:
        result['version'] = struct.unpack('<B', data[offset:offset+1])[0]
        offset += 1
    if len(data) >= offset + 4:
        result['timestamp'] = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
    if len(data) >= offset + 4:
        result['current_a'] = struct.unpack('<f', data[offset:offset+4])[0]
        offset += 4
    if len(data) >= offset + 4:
        result['voltage_v'] = struct.unpack('<f', data[offset:offset+4])[0]
        offset += 4
    if len(data) >= offset + 4:
        result['power_w'] = struct.unpack('<f', data[offset:offset+4])[0]
        offset += 4
    if len(data) >= offset + 2:
        result['raw_adc'] = struct.unpack('<h', data[offset:offset+2])[0]

    return result

def decode_payload(peripheral_id: int, payload: bytes) -> str:
    """Attempt to decode payload based on peripheral ID and size"""
    try:
        payload_len = len(payload)
        peripheral_name = next((k for k, v in PERIPHERALS.items() if v == peripheral_id), "UNKNOWN")

        if peripheral_id == PERIPHERALS["SYSTEM"]:
            # Single byte responses are usually ACKs
            if payload_len == 1:
                return f"ACK command: 0x{payload[0]:02X}"
            # Try heartbeat first (smaller message)
            elif payload_len <= SIZE_HEARTBEAT:
                data = unpack_heartbeat(payload)
                return f"Heartbeat: {data}"
            # Otherwise try status (can handle partial)
            else:
                data = unpack_status(payload)
                return f"Status: {data}"

        elif peripheral_id == PERIPHERALS["LORA_915"]:
            data = unpack_lora_data(payload)
            return f"LoRa 915: {data}"

        elif peripheral_id == PERIPHERALS["LORA_433"]:
            data = unpack_lora_data(payload)  # Same structure
            return f"LoRa 433: {data}"

        elif peripheral_id == PERIPHERALS["BAROMETER"]:
            data = unpack_barometer_data(payload)
            return f"Barometer: {data}"

        elif peripheral_id == PERIPHERALS["CURRENT"]:
            data = unpack_current_data(payload)
            return f"Current Sensor: {data}"

        else:
            return f"Unknown peripheral 0x{peripheral_id:02X}"

    except Exception as e:
        return f"Decode error: {e}"

class SerialClient:
    def __init__(self):
        self.ser = None

    def list_ports(self):
        if list_ports is None:
            return []
        return [p.device for p in list_ports.comports()]

    def connect(self, port: str, baudrate: int = DEFAULT_BAUD):
        if serial is None:
            raise RuntimeError("pyserial not installed. Run: pip install pyserial")
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=READ_TIMEOUT_S,
            write_timeout=READ_TIMEOUT_S,
            # Important: Disable input/output buffering to prevent cumulative lag
            xonxoff=False,      # No software flow control
            rtscts=False,       # No hardware flow control
            dsrdtr=False        # No DSR/DTR flow control
        )
        # Flush any stale data from previous session
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def is_open(self):
        return bool(self.ser and self.ser.is_open)

    def flush_input_buffer(self):
        """Flush any stale data from input buffer to prevent desync"""
        if self.is_open():
            waiting = self.ser.in_waiting
            if waiting > 0:
                flushed = self.ser.read(waiting)
                print(f"[INFO] Flushed {waiting} bytes from input buffer: {flushed[:20].hex()}{'...' if len(flushed) > 20 else ''}")

    def resync_to_response_byte(self):
        """Search for next RESPONSE_BYTE to recover from desync. Returns True if found."""
        if not self.is_open():
            return False

        print(f"[RESYNC] Searching for RESPONSE_BYTE (0x7D)...")
        searched = 0
        start_time = time.time()

        while time.time() - start_time < 2.0 and searched < 1000:
            b = self.ser.read(1)
            if not b:
                time.sleep(0.01)
                continue
            searched += 1
            if b[0] == RESPONSE_BYTE:
                print(f"[RESYNC] Found RESPONSE after {searched} bytes")
                # We've consumed the RESPONSE byte, need to handle this in recv_frame
                return True

        print(f"[RESYNC] Failed to find RESPONSE after {searched} bytes")
        self.ser.reset_input_buffer()  # Give up, flush everything
        return False

    # Compose protocol frame and send (NEW PROTOCOL)
    # Format: [HELLO][PERIPHERAL_ID][LENGTH][COMMAND][data...][GOODBYE]
    def send_command(self, peripheral_id: int, command: int, data: bytes = b'') -> None:
        if not self.is_open():
            raise RuntimeError("Serial port not open")
        payload = bytes([command]) + data
        length = len(payload)
        frame = bytes([HELLO_BYTE, peripheral_id, length]) + payload + bytes([GOODBYE_BYTE])
        # Don't reset input buffer - continuous reader thread handles incoming data
        self.ser.write(frame)
        self.ser.flush()

    # Receive a single full response frame (NEW PROTOCOL)
    # Format: [RESPONSE][PERIPHERAL_ID][LENGTH][payload...][GOODBYE]
    def recv_frame(self, response_already_consumed=False):
        if not self.is_open():
            raise RuntimeError("Serial port not open")
        start_time = time.time()

        # Wait for RESPONSE_BYTE (0x7D, NOT HELLO_BYTE!) unless already consumed by resync
        if not response_already_consumed:
            skipped_bytes = []
            while time.time() - start_time < RX_TOTAL_TIMEOUT_S:
                b = self.ser.read(1)
                if not b:
                    continue
                if b[0] == RESPONSE_BYTE:
                    # Log all skipped bytes if any (helps debug ESP serial pollution)
                    if skipped_bytes:
                        skipped_hex = ' '.join(f'{x:02X}' for x in skipped_bytes)
                        skipped_ascii = ''.join(chr(x) if 32 <= x < 127 else '.' for x in skipped_bytes)
                        print(f"[WARNING] Skipped {len(skipped_bytes)} bytes before RESPONSE: [{skipped_hex}] ASCII: '{skipped_ascii}'")
                    break
                # Collect unexpected bytes for diagnostics
                skipped_bytes.append(b[0])
            else:
                raise TimeoutError("Timed out waiting for RESPONSE_BYTE (0x7D)")

        # Read Peripheral ID, Length
        hdr = self.ser.read(2)
        if len(hdr) != 2:
            raise TimeoutError("Timed out reading header (PERIPHERAL_ID, LENGTH)")
        pid, length = hdr[0], hdr[1]

        # Read payload - be patient, read in chunks if needed
        payload = b""
        if length > 0:
            bytes_remaining = length
            read_deadline = time.time() + 2.0  # 2 second total timeout for payload

            while bytes_remaining > 0 and time.time() < read_deadline:
                chunk = self.ser.read(bytes_remaining)
                if chunk:
                    payload += chunk
                    bytes_remaining -= len(chunk)
                else:
                    # No data available, brief wait before retry
                    time.sleep(0.01)

            # Check if we got everything
            if len(payload) != length:
                print(f"[WARNING] Partial payload: expected {length} bytes, got {len(payload)}")
                raise TimeoutError(f"Partial payload: expected {length} bytes, got {len(payload)}")

        # Read GOODBYE
        gb = self.ser.read(1)
        if len(gb) != 1 or gb[0] != GOODBYE_BYTE:
            # Bad GOODBYE - frame is corrupted, try to resync
            if gb:
                print(f"[ERROR] Invalid GOODBYE byte (got 0x{gb[0]:02X})")
                raise TimeoutError(f"Missing/invalid GOODBYE (expected 0x7F, got 0x{gb[0]:02X})")
            else:
                print(f"[ERROR] Invalid GOODBYE byte (got nothing)")
                raise TimeoutError(f"Missing/Invalid GOODBYE (expected 0x7F, got nothing)")

        # DON'T read extra bytes after GOODBYE - they may be the next valid frame!
        # Just log if we detect them (helps with debugging)
        time.sleep(0.01)  # 10ms delay to let any trailing bytes arrive
        garbage = self.ser.in_waiting
        if garbage > 0:
            # Peek at what's there for debugging, but DON'T consume it
            print(f"[INFO] {garbage} bytes already waiting (likely next frame)")

        return pid, payload

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Embedded Comm Test GUI")
        self.geometry("950x650")
        self.resizable(True, True)

        self.client = SerialClient()
        self.reader_thread = None
        self.reader_running = False

        # Polling state
        self.polling_active = False
        self.polling_thread = None
        self.last_rx_time = 0
        self.pending_response = False

        # Polling statistics
        self.poll_stats = {
            'total': 0,
            'success': 0,
            'timeout': 0,
            'error': 0,
            'partial': 0
        }

        # Web GUI integration
        self.web_gui_enabled = False
        self.web_gui_url = "http://localhost:5000/api/telemetry/push"

        # Throughput tracking per peripheral
        self.throughput_stats = {}  # {peripheral_name: {'tx': bytes, 'rx': bytes}}
        self.throughput_log_path = os.path.join(os.path.dirname(__file__), "logs", "throughput.csv")

        self._build_ui()
        self._refresh_ports()

        # Register cleanup handler for when window closes
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _on_closing(self):
        """Handle window close event - save throughput log and cleanup"""
        # Save throughput data one final time
        self._save_throughput_log()

        # Stop reader thread
        self.reader_running = False

        # Stop polling if active
        self.polling_active = False

        # Close serial connection
        if self.client.is_open():
            self.client.close()

        # Destroy window
        self.destroy()

    def _build_ui(self):
        # ---- Top: Serial connection ----
        frm_conn = ttk.LabelFrame(self, text="Serial Connection")
        frm_conn.pack(fill="x", padx=10, pady=8)

        ttk.Label(frm_conn, text="Port:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.port_var = tk.StringVar()
        self.cmb_ports = ttk.Combobox(frm_conn, textvariable=self.port_var, width=24, state="readonly")
        self.cmb_ports.grid(row=0, column=1, padx=6, pady=6)
        ttk.Button(frm_conn, text="Refresh", command=self._refresh_ports).grid(row=0, column=2, padx=6, pady=6)

        ttk.Label(frm_conn, text="Baud:").grid(row=0, column=3, padx=6, pady=6, sticky="w")
        self.baud_var = tk.StringVar(value=str(DEFAULT_BAUD))
        ttk.Entry(frm_conn, textvariable=self.baud_var, width=10).grid(row=0, column=4, padx=6, pady=6)

        self.btn_connect = ttk.Button(frm_conn, text="Connect", command=self._connect)
        self.btn_connect.grid(row=0, column=5, padx=6, pady=6)
        self.btn_disconnect = ttk.Button(frm_conn, text="Disconnect", command=self._disconnect, state="disabled")
        self.btn_disconnect.grid(row=0, column=6, padx=6, pady=6)

        # ---- Middle: Command builder ----
        frm_cmd = ttk.LabelFrame(self, text="Message Builder")
        frm_cmd.pack(fill="x", padx=10, pady=8)

        ttk.Label(frm_cmd, text="Peripheral:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.peripheral_var = tk.StringVar(value="SYSTEM")
        self.cmb_peripheral = ttk.Combobox(frm_cmd, textvariable=self.peripheral_var, values=list(PERIPHERALS.keys()), state="readonly", width=20)
        self.cmb_peripheral.grid(row=0, column=1, padx=6, pady=6)
        self.cmb_peripheral.bind("<<ComboboxSelected>>", lambda e: self._update_commands())

        ttk.Label(frm_cmd, text="Command:").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.command_var = tk.StringVar()
        self.cmb_command = ttk.Combobox(frm_cmd, textvariable=self.command_var, state="readonly", width=28)
        self.cmb_command.grid(row=0, column=3, padx=6, pady=6)

        ttk.Label(frm_cmd, text="Payload (hex bytes):").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        self.payload_var = tk.StringVar()
        self.ent_payload = ttk.Entry(frm_cmd, textvariable=self.payload_var, width=60)
        self.ent_payload.grid(row=1, column=1, columnspan=3, padx=6, pady=6, sticky="we")

        self.btn_send = ttk.Button(frm_cmd, text="Send", command=self._send)
        self.btn_send.grid(row=0, column=4, padx=6, pady=6, sticky="e")

        # Quick actions (updated for new protocol)
        self.btn_wakeup = ttk.Button(frm_cmd, text="SYS_WAKEUP", command=lambda: self._quick_send("SYSTEM", "SYSTEM_WAKEUP"))
        self.btn_wakeup.grid(row=1, column=4, padx=6, pady=6, sticky="e")
        self.btn_getall = ttk.Button(frm_cmd, text="GET_ALL", command=lambda: self._quick_send("LORA_915", "GET_ALL"))
        self.btn_getall.grid(row=1, column=5, padx=6, pady=6, sticky="e")

        # ---- Manual Polling (Pi-side repeated requests) ----
        frm_polling = ttk.LabelFrame(self, text="Manual Polling (Pi-side)")
        frm_polling.pack(fill="x", padx=10, pady=4)

        ttk.Label(frm_polling, text="Interval (ms):").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.polling_interval_var = tk.StringVar(value="1000")
        ttk.Entry(frm_polling, textvariable=self.polling_interval_var, width=10).grid(row=0, column=1, padx=6, pady=6)

        self.btn_start_polling = ttk.Button(frm_polling, text="Start Polling", command=self._start_polling)
        self.btn_start_polling.grid(row=0, column=2, padx=6, pady=6)

        self.btn_stop_polling = ttk.Button(frm_polling, text="Stop Polling", command=self._stop_polling, state="disabled")
        self.btn_stop_polling.grid(row=0, column=3, padx=6, pady=6)

        self.lbl_polling_status = ttk.Label(frm_polling, text="Status: Idle", foreground="gray")
        self.lbl_polling_status.grid(row=0, column=4, padx=12, pady=6, sticky="w")

        # Statistics display (second row)
        self.lbl_polling_stats = ttk.Label(frm_polling, text="Stats: -", foreground="blue")
        self.lbl_polling_stats.grid(row=1, column=0, columnspan=6, padx=6, pady=6, sticky="w")

        # ---- Web GUI Integration ----
        frm_webgui = ttk.LabelFrame(self, text="Web GUI Integration")
        frm_webgui.pack(fill="x", padx=10, pady=4)

        self.webgui_enabled_var = tk.BooleanVar(value=False)
        self.chk_webgui = ttk.Checkbutton(frm_webgui, text="Send data to Web GUI", variable=self.webgui_enabled_var, command=self._toggle_webgui)
        self.chk_webgui.grid(row=0, column=0, padx=6, pady=6, sticky="w")

        ttk.Label(frm_webgui, text="URL:").grid(row=0, column=1, padx=6, pady=6, sticky="w")
        self.webgui_url_var = tk.StringVar(value="http://localhost:5000/api/telemetry/push")
        ttk.Entry(frm_webgui, textvariable=self.webgui_url_var, width=40).grid(row=0, column=2, padx=6, pady=6)

        self.lbl_webgui_status = ttk.Label(frm_webgui, text="Status: Disabled", foreground="gray")
        self.lbl_webgui_status.grid(row=0, column=3, padx=12, pady=6, sticky="w")

        # ---- Presets ----
        frm_presets = ttk.LabelFrame(self, text="Presets")
        frm_presets.pack(fill="x", padx=10, pady=4)
        ttk.Button(frm_presets, text="Save Preset", command=self._save_preset).grid(row=0, column=0, padx=6, pady=6)
        ttk.Button(frm_presets, text="Load Preset", command=self._load_preset).grid(row=0, column=1, padx=6, pady=6)

        # ---- Bottom: Log/Output ----
        frm_log = ttk.LabelFrame(self, text="Log")
        frm_log.pack(fill="both", expand=True, padx=10, pady=8)

        # Log control buttons
        frm_log_controls = ttk.Frame(frm_log)
        frm_log_controls.pack(fill="x", padx=6, pady=(6, 0))
        ttk.Button(frm_log_controls, text="Clear Log", command=self._clear_log).pack(side="left", padx=6)

        self.auto_clear_log_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_log_controls, text="Auto-clear before send", variable=self.auto_clear_log_var).pack(side="left", padx=12)

        self.txt_log = tk.Text(frm_log, height=20, wrap="word")
        self.txt_log.pack(fill="both", expand=True, padx=6, pady=6)
        self._log("Ready. Select a port and click Connect.")

        self._update_commands()

    def _refresh_ports(self):
        ports = self.client.list_ports()
        self.cmb_ports["values"] = ports
        if ports:
            self.port_var.set(ports[0])

    def _connect(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("Port", "Please select a serial port.")
            return
        try:
            baud = int(self.baud_var.get())
        except ValueError:
            messagebox.showerror("Baud", "Baud rate must be an integer.")
            return
        try:
            self.client.connect(port, baud)
            self.btn_connect["state"] = "disabled"
            self.btn_disconnect["state"] = "normal"
            self._log(f"Connected to {port} @ {baud} baud.")

            # Start continuous reader thread
            self.reader_running = True
            self.reader_thread = threading.Thread(target=self._continuous_reader, daemon=True)
            self.reader_thread.start()
            self._log("[Reader thread started]")
        except Exception as e:
            messagebox.showerror("Connect", str(e))

    def _disconnect(self):
        # Stop reader thread first
        self.reader_running = False
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=1.0)

        self.client.close()
        self.btn_connect["state"] = "normal"
        self.btn_disconnect["state"] = "disabled"
        self._log("Disconnected.")

    def _continuous_reader(self):
        """Continuously read from serial port in background thread"""
        last_buffer_check = time.time()

        while self.reader_running and self.client.is_open():
            try:
                # Periodic buffer health check (every 5 seconds)
                if time.time() - last_buffer_check > 5.0:
                    waiting = self.client.ser.in_waiting
                    if waiting > 500:  # More than 500 bytes waiting is suspicious
                        self._log_safe(f"[WARNING] Large buffer backlog ({waiting} bytes) - possible desync")
                        # Reset buffer to recover from desync
                        self.client.ser.reset_input_buffer()
                        self._log_safe(f"[INFO] Input buffer reset to recover sync")
                    last_buffer_check = time.time()

                # Keep reading ALL available frames without sleeping between them
                # This prevents buffer buildup when ESP sends multiple frames rapidly
                frames_this_iteration = 0
                while self.client.ser.in_waiting > 0 and frames_this_iteration < 20:
                    pid, rx_payload = self.client.recv_frame()
                    pid_name = next((k for k, v in PERIPHERALS.items() if v == pid), f"UNKNOWN_0x{pid:02X}")
                    self._log_safe(f"______________________________________________________________")
                    self._log_safe(f"RX: From {pid_name}(0x{pid:02X}) Length={len(rx_payload)} bytes")
                    self._log_safe(f"    Raw: {hexdump(rx_payload)}")

                    # Attempt to decode the payload
                    decoded = decode_payload(pid, rx_payload)
                    self._log_safe(f"    Decoded: {decoded}")

                    # Extract structured data and send to web GUI
                    threading.Thread(target=self._extract_and_send_telemetry, args=(pid, rx_payload), daemon=True).start()

                    # Track RX throughput (response frame = 1 + 1 + 1 + payload_len + 1)
                    rx_frame_len = 1 + 1 + 1 + len(rx_payload) + 1
                    self._update_throughput(pid_name, 'rx', rx_frame_len)

                    # Track statistics if polling is active (only count first frame as "the response")
                    if self.polling_active and self.pending_response and frames_this_iteration == 0:
                        # Check if response was partial (look for 'partial': True pattern)
                        is_partial = "'partial': True" in decoded or '"partial": true' in decoded.lower()
                        if is_partial:
                            self.poll_stats['partial'] += 1
                        else:
                            self.poll_stats['success'] += 1
                        self._update_poll_stats()

                    # Mark response received for polling logic
                    self.last_rx_time = time.time()
                    self.pending_response = False

                    frames_this_iteration += 1

                # If we processed frames, log it and yield briefly
                if frames_this_iteration > 0:
                    if frames_this_iteration > 1:
                        self._log_safe(f"[Processed {frames_this_iteration} frames in burst]")
                    time.sleep(0.001)  # 1ms yield after processing
                else:
                    # No data available, sleep longer to avoid busy-waiting
                    time.sleep(0.01)  # 10ms polling interval

            except Exception as e:
                if self.reader_running:  # Only log errors if we're still supposed to be running
                    self._log_safe(f"RX ERROR: {e}")

                    # Try to resync after error
                    if "GOODBYE" in str(e) or "Partial" in str(e):
                        if self.client.resync_to_response_byte():
                            self._log_safe(f"[RECOVERY] Successfully resynced to next frame")
                        else:
                            self._log_safe(f"[RECOVERY] Could not resync, flushed buffer")

                    # Track error/timeout in polling stats
                    if self.polling_active and self.pending_response:
                        if 'timeout' in str(e).lower() or 'timed out' in str(e).lower():
                            self.poll_stats['timeout'] += 1
                        else:
                            self.poll_stats['error'] += 1
                        self._update_poll_stats()

                    self.pending_response = False  # Clear pending flag on error
                    time.sleep(0.1)  # Brief delay before retrying

        self._log_safe("[Reader thread stopped]")

    def _update_commands(self):
        periph = self.peripheral_var.get()
        cmds = list(PERIPHERAL_COMMANDS.get(periph, {}).keys())
        self.cmb_command["values"] = cmds
        if cmds:
            self.command_var.set(cmds[0])
        # Clear payload by default (command will be sent as first byte automatically)
        self.payload_var.set("")

    def _quick_send(self, periph_name: str, command_name: str):
        self.peripheral_var.set(periph_name)
        self._update_commands()
        self.command_var.set(command_name)
        self.payload_var.set("")  # No extra data needed
        self._send()

    def _send(self):
        if not self.client.is_open():
            messagebox.showwarning("Serial", "Not connected.")
            return

        periph_name = self.peripheral_var.get()
        periph_id = PERIPHERALS[periph_name]
        cmd_name = self.command_var.get()

        # Get command ID from the command name
        cmd_dict = PERIPHERAL_COMMANDS.get(periph_name, {})
        if cmd_name not in cmd_dict:
            messagebox.showerror("Command", f"Unknown command: {cmd_name}")
            return
        cmd_id = cmd_dict[cmd_name]

        # Parse optional extra data (hex bytes)
        payload_text = self.payload_var.get().strip()
        try:
            # Special handling for SET_POLL_RATE - expect interval in milliseconds as decimal
            if cmd_name == "SET_POLL_RATE":
                if payload_text:
                    interval_ms = int(payload_text)
                    if interval_ms < 0 or interval_ms > 65535:
                        messagebox.showerror("Payload", "Interval must be 0-65535 ms")
                        return
                    # Pack as little-endian 2-byte unsigned integer
                    extra_data = struct.pack('<H', interval_ms)
                else:
                    messagebox.showerror("Payload", "SET_POLL_RATE requires interval in ms (e.g., 1000)")
                    return
            elif cmd_name == "STOP_POLL":
                # STOP_POLL is just SET_POLL_RATE with 0ms
                extra_data = struct.pack('<H', 0)
            else:
                # Normal hex byte parsing
                extra_data = parse_hex_bytes(payload_text) if payload_text else b''
        except ValueError as e:
            messagebox.showerror("Payload", f"Invalid interval: {e}")
            return
        except Exception as e:
            messagebox.showerror("Payload", str(e))
            return

        # Auto-clear log if enabled
        if self.auto_clear_log_var.get():
            self._clear_log()

        # Disable send button during transmission
        self.btn_send.config(state="disabled")

        # Run serial I/O in a separate thread to keep GUI responsive
        thread = threading.Thread(target=self._send_thread, args=(periph_name, periph_id, cmd_name, cmd_id, extra_data), daemon=True)
        thread.start()

    def _send_thread(self, periph_name, periph_id, cmd_name, cmd_id, extra_data):
        """Background thread for serial transmission - keeps GUI responsive"""
        try:
            # NEW PROTOCOL: Send command using send_command method
            # Frame format: [HELLO][PERIPHERAL_ID][LENGTH][COMMAND][data...][GOODBYE]
            payload = bytes([cmd_id]) + extra_data
            total_len = 1 + 1 + 1 + len(payload) + 1  # HELLO + ID + LEN + payload + GOODBYE
            self._log_safe(f"TX: Peripheral={periph_name}(0x{periph_id:02X}) Command={cmd_name}(0x{cmd_id:02X}) ExtraData={len(extra_data)} bytes")

            self.pending_response = True  # Mark that we're waiting for a response
            self.client.send_command(periph_id, cmd_id, extra_data)

            # Track TX throughput
            self._update_throughput(periph_name, 'tx', total_len)
            # Note: Response will be received and displayed by the continuous reader thread

        except Exception as e:
            self._log_safe(f"TX ERROR: {e}")
            self.pending_response = False
        finally:
            # Re-enable send button (must use after() for thread-safe GUI update)
            self.after(0, lambda: self.btn_send.config(state="normal"))

    def _log_safe(self, msg):
        """Thread-safe logging to GUI text widget"""
        self.after(0, lambda: self._log(msg))

    def _save_preset(self):
        preset = {
            "port": self.port_var.get(),
            "baud": self.baud_var.get(),
            "peripheral": self.peripheral_var.get(),
            "command": self.command_var.get(),
            "payload": self.payload_var.get(),
        }
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], title="Save Preset")
        if not path:
            return
        try:
            with open(path, "w") as f:
                json.dump(preset, f, indent=2)
            self._log(f"Preset saved to {path}")
        except Exception as e:
            messagebox.showerror("Save Preset", str(e))

    def _load_preset(self):
        path = filedialog.askopenfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], title="Load Preset")
        if not path:
            return
        try:
            with open(path, "r") as f:
                preset = json.load(f)
            self.port_var.set(preset.get("port", self.port_var.get()))
            self.baud_var.set(preset.get("baud", self.baud_var.get()))
            self.peripheral_var.set(preset.get("peripheral", "SYSTEM"))
            self._update_commands()
            self.command_var.set(preset.get("command", self.command_var.get()))
            self.payload_var.set(preset.get("payload", ""))
            self._log(f"Preset loaded from {path}")
        except Exception as e:
            messagebox.showerror("Load Preset", str(e))

    def _start_polling(self):
        """Start auto-polling with the current command"""
        if not self.client.is_open():
            messagebox.showwarning("Polling", "Not connected to serial port.")
            return

        try:
            interval_ms = int(self.polling_interval_var.get())
            if interval_ms < 50:
                messagebox.showerror("Polling", "Interval must be at least 50ms")
                return
        except ValueError:
            messagebox.showerror("Polling", "Interval must be a valid integer (milliseconds)")
            return

        # Get current command settings
        periph_name = self.peripheral_var.get()
        periph_id = PERIPHERALS[periph_name]
        cmd_name = self.command_var.get()

        cmd_dict = PERIPHERAL_COMMANDS.get(periph_name, {})
        if cmd_name not in cmd_dict:
            messagebox.showerror("Polling", f"Unknown command: {cmd_name}")
            return
        cmd_id = cmd_dict[cmd_name]

        payload_text = self.payload_var.get().strip()
        try:
            extra_data = parse_hex_bytes(payload_text) if payload_text else b''
        except Exception as e:
            messagebox.showerror("Polling", str(e))
            return

        # Start polling
        self.polling_active = True
        self.pending_response = False
        self.btn_start_polling.config(state="disabled")
        self.btn_stop_polling.config(state="normal")
        self.lbl_polling_status.config(text="Status: Running", foreground="green")

        self._log(f"[POLLING STARTED] Interval={interval_ms}ms Command={periph_name}:{cmd_name}")

        # Launch polling thread
        self.polling_thread = threading.Thread(
            target=self._polling_loop,
            args=(periph_name, periph_id, cmd_name, cmd_id, extra_data, interval_ms),
            daemon=True
        )
        self.polling_thread.start()

    def _stop_polling(self):
        """Stop auto-polling"""
        self.polling_active = False
        self.btn_start_polling.config(state="normal")
        self.btn_stop_polling.config(state="disabled")
        self.lbl_polling_status.config(text="Status: Stopped", foreground="red")

        # Log final stats
        total = self.poll_stats['total']
        if total > 0:
            success_pct = (self.poll_stats['success'] / total) * 100
            self._log(f"[POLLING STOPPED] Final Stats: {self.poll_stats['success']}/{total} success ({success_pct:.1f}%)")
        else:
            self._log("[POLLING STOPPED]")

        # Reset statistics
        self.poll_stats = {'total': 0, 'success': 0, 'timeout': 0, 'error': 0, 'partial': 0}
        self.lbl_polling_stats.config(text="Stats: -")

    def _polling_loop(self, periph_name, periph_id, cmd_name, cmd_id, extra_data, interval_ms):
        """Background thread that continuously sends commands at intervals"""
        interval_sec = interval_ms / 1000.0
        count = 0

        while self.polling_active and self.client.is_open():
            # Wait for any pending response before sending next
            timeout_start = time.time()
            while self.pending_response and (time.time() - timeout_start < 2.0):
                time.sleep(0.01)  # Wait 10ms and check again

            if not self.polling_active:
                break

            # Send the command
            try:
                count += 1
                self.poll_stats['total'] += 1
                payload = bytes([cmd_id]) + extra_data

                # Auto-clear log if enabled (thread-safe)
                if self.auto_clear_log_var.get():
                    self.after(0, self._clear_log)
                    time.sleep(0.05)  # Brief delay to let clear finish

                self._log_safe(f"[POLL {count}] TX: {periph_name}:{cmd_name}")

                self.pending_response = True
                self.client.send_command(periph_id, cmd_id, extra_data)

                # Wait for the interval
                time.sleep(interval_sec)

            except Exception as e:
                self._log_safe(f"[POLL {count}] ERROR: {e}")
                self.poll_stats['error'] += 1
                self._update_poll_stats()
                self.pending_response = False
                time.sleep(0.5)  # Brief delay on error

        self._log_safe(f"[Polling thread stopped after {count} requests]")

    def _update_poll_stats(self):
        """Update the polling statistics display (thread-safe)"""
        def update():
            total = self.poll_stats['total']
            if total == 0:
                self.lbl_polling_stats.config(text="Stats: -")
                return

            success = self.poll_stats['success']
            partial = self.poll_stats['partial']
            timeout = self.poll_stats['timeout']
            error = self.poll_stats['error']

            # Calculate percentages
            success_pct = (success / total) * 100
            partial_pct = (partial / total) * 100
            timeout_pct = (timeout / total) * 100
            error_pct = (error / total) * 100

            # Build stats string
            stats_text = f"Total: {total} | Success: {success} ({success_pct:.1f}%)"
            if partial > 0:
                stats_text += f" | Partial: {partial} ({partial_pct:.1f}%)"
            if timeout > 0:
                stats_text += f" | Timeout: {timeout} ({timeout_pct:.1f}%)"
            if error > 0:
                stats_text += f" | Error: {error} ({error_pct:.1f}%)"

            # Color based on success rate
            if success_pct >= 95:
                color = "green"
            elif success_pct >= 80:
                color = "orange"
            else:
                color = "red"

            self.lbl_polling_stats.config(text=stats_text, foreground=color)

        self.after(0, update)

    def _extract_and_send_telemetry(self, peripheral_id, payload):
        """Extract telemetry from decoded data and send to web GUI in expected format"""
        if not self.web_gui_enabled:
            return

        try:
            telemetry_row = {"time": time.time()}  # Add timestamp

            # Unpack based on peripheral type
            if peripheral_id == PERIPHERALS["BAROMETER"]:
                data = unpack_barometer_data(payload)
                if not data.get('partial'):
                    telemetry_row["temp"] = data.get('temperature_c', 0)
                    telemetry_row["pres"] = data.get('pressure_pa', 0)
                    telemetry_row["alt"] = data.get('altitude_m', 0)
                    self._send_to_webgui(telemetry_row)

            elif peripheral_id == PERIPHERALS["CURRENT"]:
                data = unpack_current_data(payload)
                if not data.get('partial'):
                    telemetry_row["volts"] = data.get('voltage_v', 0)
                    telemetry_row["curr"] = data.get('current_a', 0)
                    self._send_to_webgui(telemetry_row)

            elif peripheral_id == PERIPHERALS["LORA_915"] or peripheral_id == PERIPHERALS["LORA_433"]:
                data = unpack_lora_data(payload)
                if not data.get('partial'):
                    # LoRa/433 data doesn't directly map to flight telemetry
                    # Could add custom fields if needed
                    telemetry_row["rssi"] = data.get('rssi', 0)
                    telemetry_row["snr"] = data.get('snr', 0) if 'snr' in data else None
                    self._send_to_webgui(telemetry_row)

        except Exception as e:
            # Silently fail to avoid spam during decoding errors
            pass

    def _toggle_webgui(self):
        """Toggle web GUI integration on/off"""
        self.web_gui_enabled = self.webgui_enabled_var.get()
        if self.web_gui_enabled:
            self.lbl_webgui_status.config(text="Status: Enabled", foreground="green")
            self._log(f"[Web GUI] Enabled - sending to {self.webgui_url_var.get()}")
        else:
            self.lbl_webgui_status.config(text="Status: Disabled", foreground="gray")
            self._log("[Web GUI] Disabled")

    def _send_to_webgui(self, data_dict):
        """Send telemetry data to the web GUI via HTTP POST"""
        if not self.web_gui_enabled:
            return

        try:
            url = self.webgui_url_var.get()
            response = requests.post(url, json=data_dict, timeout=1.0)
            if response.status_code != 200:
                self._log_safe(f"[Web GUI] Error: HTTP {response.status_code}")
        except requests.exceptions.Timeout:
            pass  # Silently ignore timeouts to avoid spam
        except Exception as e:
            self._log_safe(f"[Web GUI] Error: {e}")

    def _update_throughput(self, peripheral_name, direction, num_bytes):
        """Track throughput for a peripheral (direction: 'tx' or 'rx')"""
        if peripheral_name not in self.throughput_stats:
            self.throughput_stats[peripheral_name] = {'tx': 0, 'rx': 0}

        self.throughput_stats[peripheral_name][direction] += num_bytes

        # Save to file periodically (every ~1KB to see results sooner)
        total_updates = sum(stats['tx'] + stats['rx'] for stats in self.throughput_stats.values())
        if total_updates % 1000 == 0:  # Every ~1KB
            self._save_throughput_log()

    def _save_throughput_log(self):
        """Save throughput stats to CSV file (cumulative across sessions)"""
        try:
            # Ensure logs directory exists
            os.makedirs(os.path.dirname(self.throughput_log_path), exist_ok=True)

            # Read existing totals if file exists
            existing_totals = {}
            if os.path.exists(self.throughput_log_path):
                with open(self.throughput_log_path, 'r') as f:
                    lines = f.readlines()
                    # Skip header line
                    for line in lines[1:]:
                        parts = line.strip().split(',')
                        if len(parts) == 4:
                            periph = parts[0]
                            existing_totals[periph] = {
                                'tx': int(parts[1]),
                                'rx': int(parts[2])
                            }

            # Merge current session stats with existing totals
            merged_stats = existing_totals.copy()
            for periph, stats in self.throughput_stats.items():
                if periph not in merged_stats:
                    merged_stats[periph] = {'tx': 0, 'rx': 0}
                merged_stats[periph]['tx'] += stats['tx']
                merged_stats[periph]['rx'] += stats['rx']

            # Write merged totals to CSV
            with open(self.throughput_log_path, 'w') as f:
                f.write("peripheral,tx_bytes,rx_bytes,total_bytes\n")
                for periph, stats in sorted(merged_stats.items()):
                    tx = stats['tx']
                    rx = stats['rx']
                    total = tx + rx
                    f.write(f"{periph},{tx},{rx},{total}\n")

        except Exception as e:
            # Silently fail to avoid interrupting normal operation
            pass

    def _log(self, msg: str):
        self.txt_log.insert("end", msg + "\n")
        self.txt_log.see("end")

    def _clear_log(self):
        """Clear all text from the log window"""
        self.txt_log.delete("1.0", "end")
        self._log("Log cleared.")

if __name__ == "__main__":
    app = App()
    app.mainloop()