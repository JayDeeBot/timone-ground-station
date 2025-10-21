
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
import time
import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

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
}

# Generic Commands (work for ALL peripherals: 0x00-0x0F)
GENERIC_COMMANDS = {
    "GET_ALL":     0x00,  # Get all available data from peripheral
    "GET_STATUS":  0x01,  # Get status/health of peripheral
    "RESET":       0x02,  # Reset peripheral
    "CONFIGURE":   0x03,  # Configure peripheral
}

# System-only Commands (only for PERIPHERAL_ID = 0x00: 0x20-0x2F)
SYSTEM_COMMANDS = {
    "WAKEUP":  0x20,  # Wake up system from low-power state
    "SLEEP":   0x21,  # Put system into low-power state
    "RESET_SYSTEM":  0x22,  # Reset entire ESP32
}

# Mapping of peripherals to their command sets
PERIPHERAL_COMMANDS = {
    "SYSTEM": {**GENERIC_COMMANDS, **SYSTEM_COMMANDS},
    "LORA_915": GENERIC_COMMANDS.copy(),
    "LORA_433": GENERIC_COMMANDS.copy(),
    "BAROMETER": GENERIC_COMMANDS.copy(),
    "CURRENT": GENERIC_COMMANDS.copy(),
    "AIM_1": GENERIC_COMMANDS.copy(),
    "AIM_2": GENERIC_COMMANDS.copy(),
    "AIM_3": GENERIC_COMMANDS.copy(),
    "AIM_4": GENERIC_COMMANDS.copy(),
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
        self.ser = serial.Serial(port=port, baudrate=baudrate, timeout=READ_TIMEOUT_S, write_timeout=READ_TIMEOUT_S)

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def is_open(self):
        return bool(self.ser and self.ser.is_open)

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
    def recv_frame(self):
        if not self.is_open():
            raise RuntimeError("Serial port not open")
        start_time = time.time()

        # Wait for RESPONSE_BYTE (0x7D, NOT HELLO_BYTE!)
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

        # Read payload
        payload = b""
        if length > 0:
            payload = self.ser.read(length)
            # Don't raise error on partial payload - let the unpack functions handle it
            # Just log a warning if we got less than expected
            if len(payload) != length:
                print(f"[WARNING] Partial payload: expected {length} bytes, got {len(payload)}")

        # Read GOODBYE
        gb = self.ser.read(1)
        if len(gb) != 1 or gb[0] != GOODBYE_BYTE:
            if gb:
                raise TimeoutError(f"Missing/invalid GOODBYE (expected 0x7F, got 0x{gb[0]:02X})")
            else:
                raise TimeoutError(f"Missing/Invalid GOODBYE (expected 0x7F, got nothing)")

        # Check for garbage bytes after GOODBYE (indicates ESP is sending extra data)
        # Use a small delay to allow any trailing bytes to arrive
        time.sleep(0.01)  # 10ms delay
        garbage = self.ser.in_waiting
        if garbage > 0:
            extra = self.ser.read(garbage)
            extra_hex = ' '.join(f'{x:02X}' for x in extra)
            extra_ascii = ''.join(chr(x) if 32 <= x < 127 else '.' for x in extra)
            print(f"[WARNING] {garbage} extra bytes after GOODBYE: [{extra_hex}] ASCII: '{extra_ascii}'")

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

        self._build_ui()
        self._refresh_ports()

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
        self.btn_wakeup = ttk.Button(frm_cmd, text="WAKEUP", command=lambda: self._quick_send("SYSTEM", "WAKEUP"))
        self.btn_wakeup.grid(row=1, column=4, padx=6, pady=6, sticky="e")
        self.btn_getall = ttk.Button(frm_cmd, text="GET_ALL", command=lambda: self._quick_send("LORA_915", "GET_ALL"))
        self.btn_getall.grid(row=1, column=5, padx=6, pady=6, sticky="e")

        # ---- Auto-Polling ----
        frm_polling = ttk.LabelFrame(self, text="Auto-Polling")
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

        # ---- Presets ----
        frm_presets = ttk.LabelFrame(self, text="Presets")
        frm_presets.pack(fill="x", padx=10, pady=4)
        ttk.Button(frm_presets, text="Save Preset", command=self._save_preset).grid(row=0, column=0, padx=6, pady=6)
        ttk.Button(frm_presets, text="Load Preset", command=self._load_preset).grid(row=0, column=1, padx=6, pady=6)

        # ---- Bottom: Log/Output ----
        frm_log = ttk.LabelFrame(self, text="Log")
        frm_log.pack(fill="both", expand=True, padx=10, pady=8)

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
        while self.reader_running and self.client.is_open():
            try:
                # Only try to read if there's data available (non-blocking check)
                if self.client.ser.in_waiting > 0:
                    pid, rx_payload = self.client.recv_frame()
                    pid_name = next((k for k, v in PERIPHERALS.items() if v == pid), f"UNKNOWN_0x{pid:02X}")

                    self._log_safe(f"RX: From {pid_name}(0x{pid:02X}) Length={len(rx_payload)} bytes")
                    self._log_safe(f"    Raw: {hexdump(rx_payload)}")

                    # Attempt to decode the payload
                    decoded = decode_payload(pid, rx_payload)
                    self._log_safe(f"    Decoded: {decoded}")

                    # Track statistics if polling is active
                    if self.polling_active and self.pending_response:
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
                else:
                    # No data available, sleep briefly to avoid busy-waiting
                    time.sleep(0.05)  # 50ms polling interval

            except Exception as e:
                if self.reader_running:  # Only log errors if we're still supposed to be running
                    self._log_safe(f"RX ERROR: {e}")

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
            extra_data = parse_hex_bytes(payload_text) if payload_text else b''
        except Exception as e:
            messagebox.showerror("Payload", str(e))
            return

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

    def _log(self, msg: str):
        self.txt_log.insert("end", msg + "\n")
        self.txt_log.see("end")

if __name__ == "__main__":
    app = App()
    app.mainloop()