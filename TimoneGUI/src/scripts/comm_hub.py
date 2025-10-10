#!/usr/bin/env python3
"""
comm_hub.py — Timone Ground Station communication broker

Single Python program that sits between the embedded ground‑station firmware (over
serial/USB/UART) and three GUI-facing client apps (433 LoRa, 915 LoRa, Settings).

UPDATED to match the EXACT protocol you provided:

General Message Structure (all directions)
  [SOF=0x7E][MSG_TYPE:1][LEN:1][PAYLOAD:LEN][EOF=0x7F]

Message Type Key
  0x01 → 915 MHz LoRa
  0x02 → 433 MHz LoRa
  0x03 → AiM Port 1
  0x04 → AiM Port 2
  0x05 → AiM Port 3
  0x06 → AiM Port 4
  0x07 → Settings

Settings Payload Structure (when MSG_TYPE == 0x07)
  [SETTING_KEY:1][VALUE:variable]
  The VALUE size depends on the SETTING_KEY type:
    - Float → 4 bytes (IEEE‑754 little‑endian)
    - Int   → 4 bytes (signed little‑endian)
    - Pair(Int, Int) → 8 bytes (two 4‑byte ints)
  The LEN byte in the general header MUST equal (1 + VALUE_SIZE) for settings.

Examples
- Set 915 MHz LoRa Bandwidth (SETTING_KEY=0x08, float 125.0 kHz):
  SOF 7E | TYPE 07 | LEN 05 | PAYLOAD: 08 <float32(125.0)> | EOF 7F
- Set 433 MHz LoRa Coding Rate to "4/7" (SETTING_KEY=0x0C, pair ints [4,7]):
  SOF 7E | TYPE 07 | LEN 09 | PAYLOAD: 0C <int32(4)> <int32(7)> | EOF 7F

No CRC, 1‑byte length, byte‑exact routing by MSG_TYPE.

Run:
    python3 comm_hub.py --port /dev/ttyACM0 --baud 115200 \
        --tcp-433 127.0.0.1:9401 --tcp-915 127.0.0.1:9402 --tcp-settings 127.0.0.1:9403

GUI clients speak newline‑delimited JSON. Example to send a 915 frame:
    {"to_embedded": true, "type_hex": "01", "payload_hex": "DEADBEEF"}

Jarred: this file remains asyncio‑first and single‑file as requested.
"""

from __future__ import annotations
import asyncio
import argparse
import json
import logging
import os
import struct
import sys
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List

# Optional pandas import for Excel protocol mapping
try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None

# ----------------------------
# Logging setup
# ----------------------------
LOG = logging.getLogger("comm_hub")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
LOG.addHandler(handler)
LOG.setLevel(logging.INFO)

# ----------------------------
# Protocol framing (embedded link) — EXACT per spec
# ----------------------------
# Byte layout:
#   [SOF=0x7E][MSG_TYPE:1][LEN:1][PAYLOAD:LEN][EOF=0x7F]
SOF = 0x7E
EOF = 0x7F

MSG_TYPES = {
    "915": 0x01,
    "433": 0x02,
    "AIM1": 0x03,
    "AIM2": 0x04,
    "AIM3": 0x05,
    "AIM4": 0x06,
    "SETTINGS": 0x07,
}

# Settings keys and types (for convenience helpers)
SET_KEYS = {
    # key_name         : (id, type)
    "L915_BW": (0x08, "float"),           # 7.8 .. 1625 (kHz)
    "L915_CR": (0x09, "pair_int"),        # e.g. 4/5, 4/6, 4/7, 4/8
    "L915_SF": (0x0A, "int"),             # 5..12
    "L433_BW": (0x0B, "float"),
    "L433_CR": (0x0C, "pair_int"),
    "L433_SF": (0x0D, "int"),
}

# ----------------------------
# Frame codec
# ----------------------------
class FrameCodec:
    def __init__(self):
        # reverse map for routing incoming
        self.id_to_type = {v: k for k, v in MSG_TYPES.items()}

    @staticmethod
    def _pack_value(val_type: str, value):
        if val_type == "float":
            return struct.pack("<f", float(value))
        if val_type == "int":
            return struct.pack("<i", int(value))
        if val_type == "pair_int":
            a, b = value
            return struct.pack("<ii", int(a), int(b))
        raise ValueError(f"Unknown settings value type: {val_type}")

    @staticmethod
    def _unpack_value(val_type: str, b: bytes):
        if val_type == "float":
            return struct.unpack("<f", b)[0]
        if val_type == "int":
            return struct.unpack("<i", b)[0]
        if val_type == "pair_int":
            return struct.unpack("<ii", b)
        raise ValueError(f"Unknown settings value type: {val_type}")

    def encode(self, msg_type_hex: int, payload: bytes) -> bytes:
        if not (0 <= len(payload) <= 255):
            raise ValueError("Payload too large for 1‑byte length")
        return bytes([SOF, msg_type_hex & 0xFF, len(payload) & 0xFF]) + payload + bytes([EOF])

    def encode_settings(self, key_id: int, value_type: str, value) -> bytes:
        payload_value = self._pack_value(value_type, value)
        payload = bytes([key_id & 0xFF]) + payload_value
        return self.encode(MSG_TYPES["SETTINGS"], payload)

    def try_decode_stream(self, buf: bytearray):
        """Extract frames: returns list of (msg_type, payload_bytes)."""
        out = []
        while True:
            # find SOF
            try:
                i = buf.index(SOF)
            except ValueError:
                buf.clear(); break
            if i > 0:
                del buf[:i]
            if len(buf) < 4:  # SOF + TYPE + LEN + EOF min
                break
            msg_type = buf[1]
            length = buf[2]
            need = 1 + 1 + 1 + length + 1
            if len(buf) < need:
                break
            if buf[need-1] != EOF:
                # desync; drop SOF
                del buf[0:1]
                continue
            payload = bytes(buf[3:3+length])
            out.append((msg_type, payload))
            del buf[:need]
        return out

# Byte layout (little‑endian):
#   [SOF=0x7E][channel:1][msg_id:1][len:2][payload:len][crc16:2][EOF=0x7F]
# CRC16-CCITT (0x1021) over: channel, msg_id, len, payload
SOF = 0x7E
EOF = 0x7F

# Default channels (overridable by Excel)
DEFAULT_CHANNELS = {
    "433": 0x01,
    "915": 0x02,
    "SETTINGS": 0x10,
    # Reserve A.I.M devices (1..4) if you want them later
    "AIM1": 0x21,
    "AIM2": 0x22,
    "AIM3": 0x23,
    "AIM4": 0x24,
}

# Default messages (overridable by Excel)
DEFAULT_MESSAGES = {
    # name        : (msg_id)
    "TELEMETRY"   : 0x01,
    "DOWNLINK"    : 0x02,
    "ACK"         : 0x06,
    "NACK"        : 0x15,
    "SET_PARAM"   : 0x30,
    "GET_PARAM"   : 0x31,
    "HEARTBEAT"   : 0x7A,
}

# ----------------------------
# Helpers: CRC16-CCITT (X25 poly 0x1021, init 0xFFFF, no reflect, no xorout)
# ----------------------------
def crc16_ccitt(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if (crc & 0x8000) != 0:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF

# ----------------------------
# Protocol maps (channels, messages)
# ----------------------------
@dataclass
class ProtocolMaps:
    channels: Dict[str, int]
    messages: Dict[str, int]

    @classmethod
    def from_excel(cls, path: str) -> "ProtocolMaps":
        if pd is None:
            LOG.warning("pandas not available; using defaults")
            return cls(DEFAULT_CHANNELS.copy(), DEFAULT_MESSAGES.copy())
        if not os.path.exists(path):
            LOG.warning("Excel not found at %s; using defaults", path)
            return cls(DEFAULT_CHANNELS.copy(), DEFAULT_MESSAGES.copy())
        try:
            xls = pd.ExcelFile(path)
            channels = DEFAULT_CHANNELS.copy()
            messages = DEFAULT_MESSAGES.copy()
            # channels sheet
            if any(s.lower() == "channels" for s in xls.sheet_names):
                dfc = pd.read_excel(xls, "channels")
                for _, row in dfc.iterrows():
                    name = str(row.get("name"))
                    id_hex = str(row.get("id_hex"))
                    if name and id_hex:
                        channels[name.strip().upper()] = int(id_hex, 16)
            # messages sheet
            if any(s.lower() == "messages" for s in xls.sheet_names):
                dfm = pd.read_excel(xls, "messages")
                for _, row in dfm.iterrows():
                    name = str(row.get("name"))
                    id_hex = str(row.get("id_hex"))
                    if name and id_hex:
                        messages[name.strip().upper()] = int(id_hex, 16)
            LOG.info("Loaded protocol from Excel: %d channels, %d messages", len(channels), len(messages))
            return cls(channels, messages)
        except Exception as e:
            LOG.exception("Failed reading Excel; using defaults: %s", e)
            return cls(DEFAULT_CHANNELS.copy(), DEFAULT_MESSAGES.copy())

# ----------------------------
# Frame codec
# ----------------------------
class FrameCodec:
    def __init__(self, maps: ProtocolMaps):
        self.maps = maps

    def encode(self, channel_name: str, msg_name: str, payload: bytes) -> bytes:
        ch = self.maps.channels[channel_name.upper()]
        mid = self.maps.messages[msg_name.upper()]
        header = struct.pack("<BBH", ch, mid, len(payload))
        crc = crc16_ccitt(header + payload)
        frame = bytes([SOF]) + header + payload + struct.pack("<H", crc) + bytes([EOF])
        return frame

    def try_decode_stream(self, buf: bytearray) -> List[Tuple[int, int, bytes]]:
        """
        Attempts to extract as many frames as possible from buf.
        Returns list of (channel_id, msg_id, payload).
        Consumes bytes from buf.
        """
        frames = []
        while True:
            # find SOF
            try:
                sof_idx = buf.index(SOF)
            except ValueError:
                buf.clear()
                break
            if sof_idx > 0:
                del buf[:sof_idx]
            # need at least SOF + hdr(4) + crc(2) + EOF -> 8 bytes min
            if len(buf) < 1 + 4 + 2 + 1:
                break
            # find EOF (we'll parse length properly but EOF helps sanity check)
            try:
                eof_idx = buf.index(EOF, 1)
            except ValueError:
                # No EOF yet; wait for more bytes
                break
            # parse header/length
            if len(buf) < 1 + 4:
                break
            ch = buf[1]
            mid = buf[2]
            (length,) = struct.unpack_from("<H", buf, 3)
            need = 1 + 4 + length + 2 + 1
            if len(buf) < need:
                # Not enough bytes yet
                break
            # Verify EOF at expected position
            if buf[need - 1] != EOF:
                # Desync; drop SOF and retry
                del buf[0:1]
                continue
            payload = bytes(buf[5:5 + length])
            (crc_rx,) = struct.unpack_from("<H", buf, 5 + length)
            crc_calc = crc16_ccitt(bytes(buf[1:5]) + payload)
            if crc_rx != crc_calc:
                LOG.warning("CRC mismatch (ch=%02X mid=%02X len=%d) — resync", ch, mid, length)
                del buf[0:need]
                continue
            # Good frame
            frames.append((ch, mid, payload))
            del buf[0:need]
        return frames

# ----------------------------
# Embedded link (serial or simulated)
# ----------------------------
class EmbeddedLink:
    def __init__(self, codec: FrameCodec, serial_port: Optional[str], baud: int, sim: bool = False):
        self.codec = codec
        self.serial_port = serial_port
        self.baud = baud
        self.sim = sim
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._buf = bytearray()

    async def connect(self):
        if self.sim:
            LOG.info("EmbeddedLink in simulation mode")
            return
        try:
            import serial_asyncio  # type: ignore
        except Exception as e:
            LOG.error("serial_asyncio not available: %s", e)
            raise
        self.reader, self.writer = await serial_asyncio.open_serial_connection(url=self.serial_port, baudrate=self.baud)
        LOG.info("Serial opened on %s @ %d", self.serial_port, self.baud)

    async def read_frames(self):
        if self.sim:
            await asyncio.sleep(0.05)
            return []
        assert self.reader is not None
        data = await self.reader.read(1024)
        if data:
            self._buf.extend(data)
            return self.codec.try_decode_stream(self._buf)
        return []

    async def write_raw(self, frame: bytes):
        if self.sim:
            LOG.info("[SIM] send: %s", frame.hex())
            return
        assert self.writer is not None
        self.writer.write(frame)
        await self.writer.drain()

    async def write(self, msg_type_hex: int, payload: bytes):
        frame = self.codec.encode(msg_type_hex, payload)
        await self.write_raw(frame)

    async def write_settings(self, key_id: int, value_type: str, value):
        frame = self.codec.encode_settings(key_id, value_type, value)
        await self.write_raw(frame)

# ----------------------------
# GUI client endpoints (TCP JSON)
# ----------------------------
@dataclass
class GuiEndpoint:
    name: str
    host: str
    port: int

class GuiServer:
    """Each GuiServer handles one role (433, 915, SETTINGS) and accepts multiple clients."""
    def __init__(self, name: str, host: str, port: int):
        self.name = name  # logical name
        self.host = host
        self.port = port
        self._server: Optional[asyncio.base_events.Server] = None
        self._clients: List[asyncio.StreamWriter] = []

    async def start(self):
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        LOG.info("GUI server '%s' listening on %s:%d", self.name, self.host, self.port)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        self._clients.append(writer)
        LOG.info("%s client connected: %s", self.name, addr)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line.decode('utf-8').strip())
                except json.JSONDecodeError:
                    LOG.warning("Bad JSON from %s: %r", self.name, line[:80])
                    continue
                # Bubble up by emitting an event through a queue
                await HUB_EVENTS.put(("gui_in", self.name, obj))
        except Exception as e:
            LOG.error("%s client error: %s", self.name, e)
        finally:
            LOG.info("%s client disconnected: %s", self.name, addr)
            self._clients.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def broadcast(self, obj: dict):
        data = (json.dumps(obj) + "\n").encode('utf-8')
        for w in list(self._clients):
            try:
                w.write(data)
                await w.drain()
            except Exception:
                try:
                    w.close()
                except Exception:
                    pass
                if w in self._clients:
                    self._clients.remove(w)

# ----------------------------
# Router / Hub
# ----------------------------
class Hub:
    def __init__(self, link: EmbeddedLink, servers: Dict[str, GuiServer]):
        self.link = link
        self.servers = servers  # keys: '433','915','SETTINGS'
        self.codec = link.codec

    async def pump_embedded_rx(self):
        while True:
            frames = await self.link.read_frames()
            for msg_type, payload in frames:
                type_name = self.codec.id_to_type.get(msg_type, f"0x{msg_type:02X}")
                obj = {
                    "from_embedded": True,
                    "type_hex": f"{msg_type:02X}",
                    "type_name": type_name,
                    "payload_hex": payload.hex(),
                    "ts": time.time(),
                }
                # Route by msg type
                target = None
                if msg_type == MSG_TYPES.get("915"):
                    target = self.servers.get("915")
                elif msg_type == MSG_TYPES.get("433"):
                    target = self.servers.get("433")
                elif msg_type == MSG_TYPES.get("SETTINGS"):
                    target = self.servers.get("SETTINGS")
                # broadcast to target or all if unknown
                if target:
                    await target.broadcast(obj)
                else:
                    for s in self.servers.values():
                        await s.broadcast(obj)
            await asyncio.sleep(0)

    async def pump_gui_rx(self):
        while True:
            kind, server_name, obj = await HUB_EVENTS.get()
            if kind != "gui_in":
                continue
            # Accepted forms:
            # 1) Raw:  {"to_embedded": true, "type_hex": "01", "payload_hex": "DEADBEEF"}
            # 2) Helper: {"to_embedded": true, "settings": {"key_hex":"08","type":"float","value":125.0}}
            if not obj.get("to_embedded"):
                continue
            try:
                if "settings" in obj:
                    s = obj["settings"]
                    key_id = int(str(s.get("key_hex", "0")), 16)
                    vtype = str(s.get("type"))
                    value = s.get("value")
                    await self.link.write_settings(key_id, vtype, value)
                else:
                    t_hex = int(str(obj.get("type_hex")), 16)
                    payload_hex = obj.get("payload_hex", "")
                    payload = bytes.fromhex(payload_hex) if payload_hex else b""
                    await self.link.write(t_hex, payload)
            except Exception as e:
                LOG.error("GUI→Embedded send error: %s", e)

HUB_EVENTS: asyncio.Queue = asyncio.Queue()

# ----------------------------
# Heartbeat task (optional)
# ----------------------------
async def heartbeat_task(link: EmbeddedLink, period_s: float):
    # Example heartbeat: SETTINGS key could be used for ping if defined; otherwise send empty to 433
    while True:
        try:
            await link.write(MSG_TYPES["433"], b"")
        except Exception as e:
            LOG.debug("Heartbeat send failed: %s", e)
        await asyncio.sleep(period_s):
    while True:
        try:
            await link.write_frame(channel, "HEARTBEAT", b"")
        except Exception as e:
            LOG.debug("Heartbeat send failed: %s", e)
        await asyncio.sleep(period_s)

# ----------------------------
# Command‑line and entry point
# ----------------------------

def parse_host_port(s: str) -> Tuple[str, int]:
    host, port = s.split(":", 1)
    return host, int(port)


def main():
    ap = argparse.ArgumentParser(description="Timone Ground Station comm hub (exact byte protocol)")
    ap.add_argument("--port", default=None, help="Serial port for embedded (e.g., /dev/ttyACM0)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--tcp-433", default="127.0.0.1:9401")
    ap.add_argument("--tcp-915", default="127.0.0.1:9402")
    ap.add_argument("--tcp-settings", default="127.0.0.1:9403")
    ap.add_argument("--sim", action="store_true", help="Run without serial for local dev")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    args = ap.parse_args()

    if args.verbose == 1:
        LOG.setLevel(logging.DEBUG)
    elif args.verbose >= 2:
        LOG.setLevel(logging.NOTSET)

    codec = FrameCodec()
    link = EmbeddedLink(codec, serial_port=args.port, baud=args.baud, sim=args.sim or not args.port)

    # Prepare GUI servers
    h433, p433 = parse_host_port(args.tcp_433)
    h915, p915 = parse_host_port(args.tcp_915)
    hset, pset = parse_host_port(args.tcp_settings)

    servers = {
        "433": GuiServer("433", h433, p433),
        "915": GuiServer("915", h915, p915),
        "SETTINGS": GuiServer("SETTINGS", hset, pset),
    }

    async def runner():
        await link.connect()
        await asyncio.gather(*(srv.start() for srv in servers.values()))
        hub = Hub(link, servers)
        tasks = [
            asyncio.create_task(hub.pump_embedded_rx()),
            asyncio.create_task(hub.pump_gui_rx()),
            asyncio.create_task(heartbeat_task(link, 2.0)),
        ]
        LOG.info("Comm hub running (sim=%s).", args.sim or not args.port)
        await asyncio.gather(*tasks)

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        LOG.info("Shutting down…")


if __name__ == "__main__":
    main()
