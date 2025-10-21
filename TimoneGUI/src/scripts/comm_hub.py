#!/usr/bin/env python3
"""
comm_hub.py — Timone Ground Station communication broker

Single Python program that sits between the embedded ground‑station firmware (over
serial/USB/UART) and GUI-facing client apps for testing and debugging.

UPDATED to match the NEW PERIPHERAL-BASED PROTOCOL:

Message Structure:
  Pi → ESP32 (Command):  [HELLO=0x7E][PERIPHERAL_ID][LENGTH][COMMAND][optional data...][GOODBYE=0x7F]
  ESP32 → Pi (Response): [RESPONSE=0x7D][PERIPHERAL_ID][LENGTH][data...][GOODBYE=0x7F]

Peripheral IDs:
  0x00 → SYSTEM (ESP32 control)
  0x01 → LORA_915 (915MHz LoRa)
  0x02 → LORA_433 (433MHz LoRa, also called RADIO_433)
  0x03 → BAROMETER (MS5607)
  0x04 → CURRENT (Current/voltage sensor)
  0x10-0x13 → AIM_1 to AIM_4 (future)

Generic Commands (all peripherals):
  0x00 → CMD_GET_ALL (get all data from peripheral)
  0x01 → CMD_GET_STATUS (get status/health)
  0x02 → CMD_RESET (reset peripheral)
  0x03 → CMD_CONFIGURE (configure peripheral)

System Commands (PERIPHERAL_ID=0x00 only):
  0x20 → CMD_SYSTEM_WAKEUP (wake from low-power)
  0x21 → CMD_SYSTEM_SLEEP (enter low-power)
  0x22 → CMD_SYSTEM_RESET (reset ESP32)

Examples:
  Get LoRa data:  [0x7E][0x01][0x01][0x00][0x7F]
  Wake system:    [0x7E][0x00][0x01][0x20][0x7F]
  Get barometer:  [0x7E][0x03][0x01][0x00][0x7F]

Run:
    python3 comm_hub.py --port /dev/ttyACM0 --baud 115200

GUI clients (if implemented) speak newline‑delimited JSON.
"""

from __future__ import annotations
import asyncio
import argparse
import json
import logging
import struct
import sys
import time
from typing import Dict, Optional, Tuple, List

# ----------------------------
# Logging setup
# ----------------------------
LOG = logging.getLogger("comm_hub")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
LOG.addHandler(handler)
LOG.setLevel(logging.INFO)

# ----------------------------
# Protocol framing (embedded link) — NEW PERIPHERAL-BASED PROTOCOL
# ----------------------------
# Framing bytes
HELLO_BYTE = 0x7E      # Start of Pi → ESP32 message
RESPONSE_BYTE = 0x7D   # Start of ESP32 → Pi message (different from HELLO to avoid echo)
GOODBYE_BYTE = 0x7F    # End of message marker

# Peripheral IDs
PERIPHERAL_ID_SYSTEM = 0x00
PERIPHERAL_ID_LORA_915 = 0x01
PERIPHERAL_ID_LORA_433 = 0x02  # 433MHz is also LoRa (same chip, different freq)
PERIPHERAL_ID_BAROMETER = 0x03
PERIPHERAL_ID_CURRENT = 0x04
PERIPHERAL_ID_AIM_1 = 0x10
PERIPHERAL_ID_AIM_2 = 0x11
PERIPHERAL_ID_AIM_3 = 0x12
PERIPHERAL_ID_AIM_4 = 0x13

# Backward compatibility
PERIPHERAL_ID_RADIO_433 = PERIPHERAL_ID_LORA_433

# Peripheral name mapping
PERIPHERAL_NAMES = {
    PERIPHERAL_ID_SYSTEM: "SYSTEM",
    PERIPHERAL_ID_LORA_915: "LORA_915",
    PERIPHERAL_ID_LORA_433: "LORA_433",
    PERIPHERAL_ID_BAROMETER: "BAROMETER",
    PERIPHERAL_ID_CURRENT: "CURRENT",
    PERIPHERAL_ID_AIM_1: "AIM_1",
    PERIPHERAL_ID_AIM_2: "AIM_2",
    PERIPHERAL_ID_AIM_3: "AIM_3",
    PERIPHERAL_ID_AIM_4: "AIM_4",
}

# Generic commands (work for ALL peripherals)
CMD_GET_ALL = 0x00       # Get all available data from peripheral
CMD_GET_STATUS = 0x01    # Get status/health of peripheral
CMD_RESET = 0x02         # Reset peripheral
CMD_CONFIGURE = 0x03     # Configure peripheral

# System-only commands (only for PERIPHERAL_ID = 0x00)
CMD_SYSTEM_WAKEUP = 0x20  # Wake up system from low-power state
CMD_SYSTEM_SLEEP = 0x21   # Put system into low-power state
CMD_SYSTEM_RESET = 0x22   # Reset entire ESP32

# Data structure sizes (for validation)
SIZE_HEARTBEAT = 6   # WireHeartbeat_t
SIZE_STATUS = 20     # WireStatus_t
SIZE_LORA = 74       # WireLoRa_t
SIZE_433 = 74        # Wire433_t (same as WireLoRa_t)
SIZE_BAROMETER = 17  # WireBarometer_t
SIZE_CURRENT = 19    # WireCurrent_t

# ----------------------------
# Data structure unpacking functions
# ----------------------------
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
    """Unpack WireStatus_t (20 bytes): version(1), uptime(4), state(1), flags(1),
       pkt_lora(2), pkt_433(2), wakeup_time(4), heap(4), chip_rev(1)"""
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
    """Unpack WireLoRa_t (74 bytes): version(1), pkt_count(2), rssi(2), snr(4), len(1), data(64)"""
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

def unpack_433_data(data: bytes) -> dict:
    """Unpack Wire433_t (74 bytes) - same as WireLoRa_t"""
    return unpack_lora_data(data)  # Same structure

def unpack_barometer_data(data: bytes) -> dict:
    """Unpack WireBarometer_t (17 bytes): version(1), timestamp(4), pressure(4), temp(4), altitude(4)"""
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
    """Unpack WireCurrent_t (19 bytes): version(1), timestamp(4), current(4), voltage(4), power(4), raw_adc(2)"""
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

# ----------------------------
# Frame codec
# ----------------------------
class FrameCodec:
    """Handles encoding/decoding of the new peripheral-based protocol"""

    def __init__(self):
        pass

    def encode_command(self, peripheral_id: int, command: int, data: bytes = b'') -> bytes:
        """Encode a command to send to ESP32
        Format: [HELLO][PERIPHERAL_ID][LENGTH][COMMAND][data...][GOODBYE]
        """
        payload = bytes([command]) + data
        if len(payload) > 255:
            raise ValueError("Payload too large for 1-byte length")

        message = bytes([
            HELLO_BYTE,
            peripheral_id & 0xFF,
            len(payload) & 0xFF
        ]) + payload + bytes([GOODBYE_BYTE])

        return message

    def try_decode_stream(self, buf: bytearray) -> List[Tuple[int, bytes]]:
        """Extract response frames from buffer.
        Returns list of (peripheral_id, payload_bytes).
        Format: [RESPONSE][PERIPHERAL_ID][LENGTH][payload...][GOODBYE]
        """
        out = []
        while True:
            # Find RESPONSE_BYTE
            try:
                i = buf.index(RESPONSE_BYTE)
            except ValueError:
                buf.clear()
                break

            if i > 0:
                del buf[:i]

            # Need at least: RESPONSE + PERIPHERAL_ID + LEN + GOODBYE (min 4 bytes)
            if len(buf) < 4:
                break

            peripheral_id = buf[1]
            length = buf[2]
            need = 1 + 1 + 1 + length + 1  # RESPONSE + ID + LEN + payload + GOODBYE

            if len(buf) < need:
                break

            if buf[need-1] != GOODBYE_BYTE:
                # Desync; drop RESPONSE_BYTE and retry
                del buf[0:1]
                continue

            payload = bytes(buf[3:3+length])
            out.append((peripheral_id, payload))
            del buf[:need]

        return out


# ----------------------------
# Embedded link (serial or simulated)
# ----------------------------
class EmbeddedLink:
    """Manages serial communication with ESP32 using the new peripheral-based protocol"""

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
        self.reader, self.writer = await serial_asyncio.open_serial_connection(
            url=self.serial_port, baudrate=self.baud
        )
        LOG.info("Serial opened on %s @ %d", self.serial_port, self.baud)

    async def read_frames(self) -> List[Tuple[int, bytes]]:
        """Read and decode frames from serial. Returns list of (peripheral_id, payload)"""
        if self.sim:
            await asyncio.sleep(0.05)
            return []
        assert self.reader is not None
        data = await self.reader.read(1024)
        if data:
            self._buf.extend(data)
            return self.codec.try_decode_stream(self._buf)
        return []

    async def send_command(self, peripheral_id: int, command: int, data: bytes = b''):
        """Send a command to a specific peripheral"""
        frame = self.codec.encode_command(peripheral_id, command, data)
        if self.sim:
            LOG.info("[SIM] send command to peripheral 0x%02X: cmd=0x%02X data=%s",
                     peripheral_id, command, data.hex() if data else "(none)")
            return
        assert self.writer is not None
        self.writer.write(frame)
        await self.writer.drain()
        LOG.debug("Sent command: peripheral=0x%02X cmd=0x%02X len=%d",
                  peripheral_id, command, len(data))

    # Convenience methods for common commands
    async def get_lora_data(self):
        """Get LoRa 915MHz data"""
        await self.send_command(PERIPHERAL_ID_LORA_915, CMD_GET_ALL)

    async def get_433_data(self):
        """Get 433MHz LoRa data"""
        await self.send_command(PERIPHERAL_ID_LORA_433, CMD_GET_ALL)

    async def get_barometer_data(self):
        """Get barometer data"""
        await self.send_command(PERIPHERAL_ID_BAROMETER, CMD_GET_ALL)

    async def get_current_data(self):
        """Get current sensor data"""
        await self.send_command(PERIPHERAL_ID_CURRENT, CMD_GET_ALL)

    async def get_system_status(self):
        """Get system status"""
        await self.send_command(PERIPHERAL_ID_SYSTEM, CMD_GET_ALL)

    async def wakeup_system(self):
        """Wake up the ESP32 system"""
        await self.send_command(PERIPHERAL_ID_SYSTEM, CMD_SYSTEM_WAKEUP)
        LOG.info("Sent system wakeup command")

    async def sleep_system(self):
        """Put ESP32 into low-power mode"""
        await self.send_command(PERIPHERAL_ID_SYSTEM, CMD_SYSTEM_SLEEP)
        LOG.info("Sent system sleep command")

    async def reset_system(self):
        """Reset the entire ESP32"""
        await self.send_command(PERIPHERAL_ID_SYSTEM, CMD_SYSTEM_RESET)
        LOG.info("Sent system reset command")

# ----------------------------
# GUI client endpoints (TCP JSON)
# ----------------------------
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
    """Central message router between ESP32 and GUI/logging"""

    def __init__(self, link: EmbeddedLink, servers: Optional[Dict[str, GuiServer]] = None):
        self.link = link
        self.servers = servers or {}  # Optional GUI servers
        self.codec = link.codec

    async def pump_embedded_rx(self):
        """Read messages from ESP32 and process them"""
        while True:
            frames = await self.link.read_frames()
            for peripheral_id, payload in frames:
                peripheral_name = PERIPHERAL_NAMES.get(peripheral_id, f"UNKNOWN_0x{peripheral_id:02X}")

                # Try to unpack and display the data
                try:
                    data_dict = self._unpack_payload(peripheral_id, payload)
                    LOG.info("Received from %s (0x%02X): %s",
                             peripheral_name, peripheral_id, data_dict)
                except Exception as e:
                    LOG.warning("Failed to unpack payload from %s: %s (raw: %s)",
                                peripheral_name, e, payload.hex())
                    data_dict = {"raw_hex": payload.hex()}

                # Create JSON object for GUI clients (if any)
                obj = {
                    "from_embedded": True,
                    "peripheral_id": peripheral_id,
                    "peripheral_name": peripheral_name,
                    "payload_hex": payload.hex(),
                    "data": data_dict,
                    "ts": time.time(),
                }

                # Route to appropriate GUI server (if configured)
                await self._route_to_gui(peripheral_id, obj)

            await asyncio.sleep(0)

    def _unpack_payload(self, peripheral_id: int, payload: bytes) -> dict:
        """Attempt to unpack payload based on peripheral ID and size"""
        payload_len = len(payload)

        if peripheral_id == PERIPHERAL_ID_SYSTEM:
            if payload_len == SIZE_HEARTBEAT:
                return unpack_heartbeat(payload)
            elif payload_len == SIZE_STATUS:
                return unpack_status(payload)
            elif payload_len == 1:
                # ACK response (e.g., wakeup acknowledgment)
                return {"ack_command": f"0x{payload[0]:02X}"}
            else:
                raise ValueError(f"Unknown system payload size: {payload_len}")

        elif peripheral_id == PERIPHERAL_ID_LORA_915:
            return unpack_lora_data(payload)

        elif peripheral_id == PERIPHERAL_ID_LORA_433:
            return unpack_433_data(payload)

        elif peripheral_id == PERIPHERAL_ID_BAROMETER:
            return unpack_barometer_data(payload)

        elif peripheral_id == PERIPHERAL_ID_CURRENT:
            return unpack_current_data(payload)

        else:
            raise ValueError(f"Unknown peripheral ID: 0x{peripheral_id:02X}")

    async def _route_to_gui(self, peripheral_id: int, obj: dict):
        """Route message to appropriate GUI server"""
        if not self.servers:
            return  # No GUI servers configured

        target = None
        if peripheral_id == PERIPHERAL_ID_LORA_915:
            target = self.servers.get("915")
        elif peripheral_id == PERIPHERAL_ID_LORA_433:
            target = self.servers.get("433")
        elif peripheral_id == PERIPHERAL_ID_SYSTEM:
            target = self.servers.get("SETTINGS")

        # Broadcast to target or all if unknown
        if target:
            await target.broadcast(obj)
        else:
            for s in self.servers.values():
                await s.broadcast(obj)

    async def pump_gui_rx(self):
        """Handle commands from GUI clients"""
        while True:
            kind, server_name, obj = await HUB_EVENTS.get()
            if kind != "gui_in":
                continue

            # Expected format: {"command": "get_lora", ...} or {"peripheral_id": 1, "command": 0, ...}
            if not obj:
                continue

            try:
                # Check for high-level command names
                cmd = obj.get("command", "").lower()
                if cmd == "get_lora" or cmd == "get_915":
                    await self.link.get_lora_data()
                elif cmd == "get_433":
                    await self.link.get_433_data()
                elif cmd == "get_barometer":
                    await self.link.get_barometer_data()
                elif cmd == "get_current":
                    await self.link.get_current_data()
                elif cmd == "get_status":
                    await self.link.get_system_status()
                elif cmd == "wakeup":
                    await self.link.wakeup_system()
                elif cmd == "sleep":
                    await self.link.sleep_system()
                elif cmd == "reset":
                    await self.link.reset_system()
                else:
                    # Raw command format
                    peripheral_id = int(obj.get("peripheral_id", 0))
                    command = int(obj.get("command_id", CMD_GET_ALL))
                    data_hex = obj.get("data_hex", "")
                    data = bytes.fromhex(data_hex) if data_hex else b''
                    await self.link.send_command(peripheral_id, command, data)

            except Exception as e:
                LOG.error("GUI→Embedded command error: %s", e)

HUB_EVENTS: asyncio.Queue = asyncio.Queue()

# ----------------------------
# Polling task for periodic data requests
# ----------------------------
async def polling_task(link: EmbeddedLink, period_s: float):
    """Periodically request data from all peripherals (optional, for testing)"""
    await asyncio.sleep(5.0)  # Wait for startup
    while True:
        try:
            # Request status from system
            await link.get_system_status()
            await asyncio.sleep(0.1)

            # Request data from sensors
            await link.get_lora_data()
            await asyncio.sleep(0.1)

            await link.get_433_data()
            await asyncio.sleep(0.1)

            await link.get_barometer_data()
            await asyncio.sleep(0.1)

            await link.get_current_data()
            await asyncio.sleep(0.1)

        except Exception as e:
            LOG.debug("Polling request failed: %s", e)

        await asyncio.sleep(period_s)

# ----------------------------
# Command‑line and entry point
# ----------------------------

def parse_host_port(s: str) -> Tuple[str, int]:
    host, port = s.split(":", 1)
    return host, int(port)


def main():
    ap = argparse.ArgumentParser(description="Timone Ground Station comm hub (peripheral-based protocol)")
    ap.add_argument("--port", default=None, help="Serial port for embedded (e.g., /dev/ttyACM0)")
    ap.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    ap.add_argument("--tcp-433", default=None, help="TCP server for 433MHz GUI (e.g., 127.0.0.1:9401)")
    ap.add_argument("--tcp-915", default=None, help="TCP server for 915MHz GUI (e.g., 127.0.0.1:9402)")
    ap.add_argument("--tcp-settings", default=None, help="TCP server for settings GUI (e.g., 127.0.0.1:9403)")
    ap.add_argument("--sim", action="store_true", help="Run without serial for local dev")
    ap.add_argument("--poll", type=float, default=0, help="Enable polling all sensors every N seconds (0=disabled)")
    ap.add_argument("--wakeup", action="store_true", help="Send wakeup command on startup")
    ap.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity (-v, -vv)")
    args = ap.parse_args()

    if args.verbose == 1:
        LOG.setLevel(logging.DEBUG)
    elif args.verbose >= 2:
        LOG.setLevel(logging.NOTSET)

    codec = FrameCodec()
    link = EmbeddedLink(codec, serial_port=args.port, baud=args.baud, sim=args.sim or not args.port)

    # Prepare GUI servers (optional)
    servers = {}
    if args.tcp_433:
        h433, p433 = parse_host_port(args.tcp_433)
        servers["433"] = GuiServer("433", h433, p433)
    if args.tcp_915:
        h915, p915 = parse_host_port(args.tcp_915)
        servers["915"] = GuiServer("915", h915, p915)
    if args.tcp_settings:
        hset, pset = parse_host_port(args.tcp_settings)
        servers["SETTINGS"] = GuiServer("SETTINGS", hset, pset)

    async def runner():
        await link.connect()

        # Start GUI servers if configured
        if servers:
            await asyncio.gather(*(srv.start() for srv in servers.values()))

        # Send wakeup command if requested
        if args.wakeup:
            LOG.info("Sending wakeup command...")
            await link.wakeup_system()
            await asyncio.sleep(0.5)  # Give ESP32 time to respond

        hub = Hub(link, servers)
        tasks = [
            asyncio.create_task(hub.pump_embedded_rx()),
        ]

        # Only start GUI pump if we have servers
        if servers:
            tasks.append(asyncio.create_task(hub.pump_gui_rx()))

        # Add polling task if enabled
        if args.poll > 0:
            tasks.append(asyncio.create_task(polling_task(link, args.poll)))
            LOG.info("Polling enabled: every %.1f seconds", args.poll)

        LOG.info("Comm hub running (sim=%s, port=%s).", args.sim or not args.port, args.port or "none")
        await asyncio.gather(*tasks)

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        LOG.info("Shutting down…")


if __name__ == "__main__":
    main()
