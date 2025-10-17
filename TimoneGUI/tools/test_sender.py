
#!/usr/bin/env python3
"""
comm_gui.py

A simple Tkinter GUI to craft and send test messages to the embedded routine over a
serial connection and print the binary response.

Protocol (from config.h / CommProtocol.cpp):
    TX/RX frame = [HELLO][PERIPHERAL_ID][LENGTH][PAYLOAD bytes ...][GOODBYE]
      - HELLO = 0x7E
      - GOODBYE = 0x7F
      - PERIPHERAL_ID = one of PERIPHERAL_ID_* (system, sensors, etc.)
      - LENGTH = number of bytes in PAYLOAD (0..255)
      - PAYLOAD = command-specific data. For most System data requests, it's 1 byte:
          CMD_GET_LORA_DATA (0x01)
          CMD_GET_433_DATA (0x02)
          CMD_GET_BAROMETER_DATA (0x03)
          CMD_GET_CURRENT_DATA (0x04)
          CMD_GET_ALL_DATA (0x05)
          CMD_GET_STATUS (0x06)
        Additional control commands are reserved:
          CMD_SYSTEM_WAKEUP (0x01), CMD_SYSTEM_STATUS(0x02), CMD_SYSTEM_SLEEP(0x03), CMD_SYSTEM_RESET(0x04)
        (Note: current firmware primarily handles the GET_* requests under PERIPHERAL_ID_SYSTEM.)

Features:
  - Serial port selection & connect/disconnect
  - Peripheral + Command dropdowns (covering all message types)
  - Optional payload editor (hex) if a command expects parameters
  - Send button constructs frame and writes to serial
  - Response reader waits for HELLO, then reads PERIPHERAL_ID, LENGTH, payload, and GOODBYE
  - Hex dump + light parsing of known binary blocks (length only; content shown as hex)
  - Convenience tools: "Ping (GET_STATUS)" & "GET_ALL_DATA" quick buttons
  - Save/Load presets for commonly used command payloads

Requirements to run locally:
  pip install pyserial

Author: ChatGPT (for Jarred & Sachin)
Date: 2025-10-17
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

# ---- Protocol constants (mirrored from config.h) ----
HELLO_BYTE   = 0x7E
GOODBYE_BYTE = 0x7F

# Peripheral IDs
PERIPHERALS = {
    "SYSTEM":         0x00,
    "LORA_915":       0x01,
    "RADIO_433":      0x02,
    "BAROMETER":      0x03,
    "CURRENT":        0x04,
    "EXTERNAL_1":     0x10,
    "EXTERNAL_2":     0x11,
    "EXTERNAL_3":     0x12,
}

# Commands for PERIPHERAL_ID_SYSTEM (data requests actually implemented in CommProtocol.cpp)
SYSTEM_DATA_COMMANDS = {
    "GET_LORA_DATA":       0x01,
    "GET_433_DATA":        0x02,
    "GET_BAROMETER_DATA":  0x03,
    "GET_CURRENT_DATA":    0x04,
    "GET_ALL_DATA":        0x05,
    "GET_STATUS":          0x06,
}

# Reserved "control" commands (not necessarily handled in current firmware)
SYSTEM_CTRL_COMMANDS = {
    "SYSTEM_WAKEUP": 0x01,
    "SYSTEM_STATUS": 0x02,
    "SYSTEM_SLEEP":  0x03,
    "SYSTEM_RESET":  0x04,
}

# For non-system peripherals, provide a placeholder space for future expansion
PERIPHERAL_COMMANDS = {
    "SYSTEM": {**SYSTEM_DATA_COMMANDS, **{"CTRL_"+k: v for k, v in SYSTEM_CTRL_COMMANDS.items()}},
    "LORA_915": {},
    "RADIO_433": {},
    "BAROMETER": {},
    "CURRENT": {},
    "EXTERNAL_1": {},
    "EXTERNAL_2": {},
    "EXTERNAL_3": {},
}

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

    # Compose protocol frame and send
    def send_frame(self, peripheral_id: int, payload: bytes) -> None:
        if not self.is_open():
            raise RuntimeError("Serial port not open")
        length = len(payload)
        frame = bytes([HELLO_BYTE, peripheral_id, length]) + payload + bytes([GOODBYE_BYTE])
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()

    # Receive a single full response frame ([HELLO][ID][LEN][PAYLOAD][GOODBYE])
    def recv_frame(self):
        if not self.is_open():
            raise RuntimeError("Serial port not open")
        start_time = time.time()

        # Wait for HELLO
        while time.time() - start_time < RX_TOTAL_TIMEOUT_S:
            b = self.ser.read(1)
            if not b:
                continue
            if b[0] == HELLO_BYTE:
                break
        else:
            raise TimeoutError("Timed out waiting for HELLO (0x7E)")

        # Read Peripheral ID, Length
        hdr = self.ser.read(2)
        if len(hdr) != 2:
            raise TimeoutError("Timed out reading header (ID, LEN)")
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
            raise TimeoutError("Missing/invalid GOODBYE (0x7F)")

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

        # Quick actions
        self.btn_ping = ttk.Button(frm_cmd, text="Ping (GET_STATUS)", command=lambda: self._quick_send("SYSTEM", "GET_STATUS"))
        self.btn_ping.grid(row=1, column=4, padx=6, pady=6, sticky="e")
        self.btn_getall = ttk.Button(frm_cmd, text="GET_ALL_DATA", command=lambda: self._quick_send("SYSTEM", "GET_ALL_DATA"))
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
        if periph == "SYSTEM" and not cmds:
            cmds = list(PERIPHERAL_COMMANDS["SYSTEM"].keys())
        self.cmb_command["values"] = cmds
        if cmds:
            self.command_var.set(cmds[0])
        # Default payload: for SYSTEM data requests, single byte matching command ID
        if periph == "SYSTEM" and self.command_var.get() in SYSTEM_DATA_COMMANDS:
            cmd_name = self.command_var.get()
            cmd_val = SYSTEM_DATA_COMMANDS[cmd_name]
            self.payload_var.set(f"{cmd_val:02X}")
        else:
            self.payload_var.set("")

    def _quick_send(self, periph_name: str, command_name: str):
        self.peripheral_var.set(periph_name)
        self._update_commands()
        self.command_var.set(command_name)
        # For system data commands, payload is the command ID
        if periph_name == "SYSTEM" and command_name in SYSTEM_DATA_COMMANDS:
            self.payload_var.set(f"{SYSTEM_DATA_COMMANDS[command_name]:02X}")
        else:
            self.payload_var.set("")
        self._send()

    def _send(self):
        if not self.client.is_open():
            messagebox.showwarning("Serial", "Not connected.")
            return
        periph_name = self.peripheral_var.get()
        periph_id = PERIPHERALS[periph_name]
        # If it's a SYSTEM data request and payload empty, auto-fill one-byte command
        payload_text = self.payload_var.get().strip()
        if periph_name == "SYSTEM" and not payload_text and self.command_var.get() in SYSTEM_DATA_COMMANDS:
            payload_text = f"{SYSTEM_DATA_COMMANDS[self.command_var.get()]:02X}"
        try:
            payload = parse_hex_bytes(payload_text)
        except Exception as e:
            messagebox.showerror("Payload", str(e))
            return
        # Compose and send
        frame = bytes([HELLO_BYTE, periph_id, len(payload)]) + payload + bytes([GOODBYE_BYTE])
        self._log(f"TX: {hexdump(frame)}  (peripheral={periph_name}, payload_len={len(payload)})")
        try:
            self.client.send_frame(periph_id, payload)
        except Exception as e:
            messagebox.showerror("Send", str(e))
            return

        # Receive a response frame
        try:
            pid, rx_payload = self.client.recv_frame()
            pid_name = next((k for k, v in PERIPHERALS.items() if v == pid), f"0x{pid:02X}")
            self._log(f"RX: HELLO {pid_name} LEN {len(rx_payload)} DATA {hexdump(rx_payload)} GOODBYE")
            # Light interpretation: if response looks like "error" blob (first 3 bytes: version, code, len)
            if len(rx_payload) >= 3 and rx_payload[0] == 1 and rx_payload[1] >= 1:
                ascii_len = rx_payload[2]
                if 3 + ascii_len <= len(rx_payload):
                    msg = rx_payload[3:3+ascii_len].decode(errors="ignore")
                    self._log(f"-> Error/versioned message: code={rx_payload[1]} msg='{msg}'")
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