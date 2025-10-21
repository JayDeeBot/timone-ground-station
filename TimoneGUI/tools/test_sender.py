
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
READ_TIMEOUT_S = 2.0   # serial read timeout per call
RX_TOTAL_TIMEOUT_S = 3.0  # overall receive timeout for a whole frame

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
    if len(data) != SIZE_HEARTBEAT:
        raise ValueError(f"Invalid heartbeat size: expected {SIZE_HEARTBEAT}, got {len(data)}")
    version, uptime, state = struct.unpack('<BIB', data)
    return {
        'version': version,
        'uptime_seconds': uptime,
        'system_state': state
    }

def unpack_status(data: bytes) -> dict:
    """Unpack WireStatus_t (20 bytes)"""
    if len(data) != SIZE_STATUS:
        raise ValueError(f"Invalid status size: expected {SIZE_STATUS}, got {len(data)}")
    values = struct.unpack('<BIBBHHIIB', data)
    return {
        'version': values[0],
        'uptime_seconds': values[1],
        'system_state': values[2],
        'sensor_flags': values[3],
        'pkt_count_lora': values[4],
        'pkt_count_433': values[5],
        'wakeup_time': values[6],
        'heap_free': values[7],
        'chip_revision': values[8]
    }

def unpack_lora_data(data: bytes) -> dict:
    """Unpack WireLoRa_t (74 bytes)"""
    if len(data) != SIZE_LORA:
        raise ValueError(f"Invalid LoRa data size: expected {SIZE_LORA}, got {len(data)}")
    values = struct.unpack('<BHhfB64s', data)
    return {
        'version': values[0],
        'packet_count': values[1],
        'rssi': values[2],
        'snr': values[3],
        'payload_length': values[4],
        'payload': values[5][:values[4]]  # trim to actual length
    }

def unpack_barometer_data(data: bytes) -> dict:
    """Unpack WireBarometer_t (17 bytes)"""
    if len(data) != SIZE_BAROMETER:
        raise ValueError(f"Invalid barometer data size: expected {SIZE_BAROMETER}, got {len(data)}")
    values = struct.unpack('<BIfff', data)
    return {
        'version': values[0],
        'timestamp': values[1],
        'pressure_pa': values[2],
        'temperature_c': values[3],
        'altitude_m': values[4]
    }

def unpack_current_data(data: bytes) -> dict:
    """Unpack WireCurrent_t (19 bytes)"""
    if len(data) != SIZE_CURRENT:
        raise ValueError(f"Invalid current data size: expected {SIZE_CURRENT}, got {len(data)}")
    values = struct.unpack('<BIfffh', data)
    return {
        'version': values[0],
        'timestamp': values[1],
        'current_a': values[2],
        'voltage_v': values[3],
        'power_w': values[4],
        'raw_adc': values[5]
    }

def decode_payload(peripheral_id: int, payload: bytes) -> str:
    """Attempt to decode payload based on peripheral ID and size"""
    try:
        payload_len = len(payload)
        peripheral_name = next((k for k, v in PERIPHERALS.items() if v == peripheral_id), "UNKNOWN")

        if peripheral_id == PERIPHERALS["SYSTEM"]:
            if payload_len == SIZE_HEARTBEAT:
                data = unpack_heartbeat(payload)
                return f"Heartbeat: {data}"
            elif payload_len == SIZE_STATUS:
                data = unpack_status(payload)
                return f"Status: {data}"
            elif payload_len == 1:
                return f"ACK command: 0x{payload[0]:02X}"
            else:
                return f"Unknown system payload (len={payload_len})"

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
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()

    # Receive a single full response frame (NEW PROTOCOL)
    # Format: [RESPONSE][PERIPHERAL_ID][LENGTH][payload...][GOODBYE]
    def recv_frame(self):
        if not self.is_open():
            raise RuntimeError("Serial port not open")
        start_time = time.time()

        # Wait for RESPONSE_BYTE (0x7D, NOT HELLO_BYTE!)
        while time.time() - start_time < RX_TOTAL_TIMEOUT_S:
            b = self.ser.read(1)
            if not b:
                continue
            if b[0] == RESPONSE_BYTE:
                break
            # Log unexpected bytes
            if b[0] != RESPONSE_BYTE:
                print(f"[DEBUG] Skipping byte: 0x{b[0]:02X} (waiting for RESPONSE=0x7D)")
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
            if len(payload) != length:
                raise TimeoutError(f"Expected {length} payload bytes, got {len(payload)}")

        # Read GOODBYE
        gb = self.ser.read(1)
        if len(gb) != 1 or gb[0] != GOODBYE_BYTE:
            raise TimeoutError(f"Missing/invalid GOODBYE (expected 0x7F, got 0x{gb[0]:02X if gb else 'none'})")

        return pid, payload

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Embedded Comm Test GUI")
        self.geometry("950x650")
        self.resizable(True, True)

        self.client = SerialClient()
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
        except Exception as e:
            messagebox.showerror("Connect", str(e))

    def _disconnect(self):
        self.client.close()
        self.btn_connect["state"] = "normal"
        self.btn_disconnect["state"] = "disabled"
        self._log("Disconnected.")

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

        # NEW PROTOCOL: Send command using send_command method
        # Frame format: [HELLO][PERIPHERAL_ID][LENGTH][COMMAND][data...][GOODBYE]
        payload = bytes([cmd_id]) + extra_data
        total_len = 1 + 1 + 1 + len(payload) + 1  # HELLO + ID + LEN + payload + GOODBYE
        self._log(f"TX: Peripheral={periph_name}(0x{periph_id:02X}) Command={cmd_name}(0x{cmd_id:02X}) ExtraData={len(extra_data)} bytes")
        try:
            self.client.send_command(periph_id, cmd_id, extra_data)
        except Exception as e:
            messagebox.showerror("Send", str(e))
            return

        # Receive a response frame (NEW PROTOCOL: uses RESPONSE_BYTE 0x7D)
        try:
            pid, rx_payload = self.client.recv_frame()
            pid_name = next((k for k, v in PERIPHERALS.items() if v == pid), f"UNKNOWN_0x{pid:02X}")

            self._log(f"RX: From {pid_name}(0x{pid:02X}) Length={len(rx_payload)} bytes")
            self._log(f"    Raw: {hexdump(rx_payload)}")

            # Attempt to decode the payload
            decoded = decode_payload(pid, rx_payload)
            self._log(f"    Decoded: {decoded}")

        except Exception as e:
            self._log(f"RX ERROR: {e}")

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

    def _log(self, msg: str):
        self.txt_log.insert("end", msg + "\n")
        self.txt_log.see("end")

if __name__ == "__main__":
    app = App()
    app.mainloop()