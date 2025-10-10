#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Timone Ground Station - Embedded Device Simulator
-------------------------------------------------
Emits binary-framed messages on a serial/pty device that match the embedded
firmware protocol. Intended to drive communicator.py + GUI listeners end-to-end.

Data sources
- LoRa915 & Radio433: read from a "flight log" text file; each line becomes a
  raw payload snippet. RSSI and SNR are parsed if present (e.g., "RSSI:-90, SNR:12 | ...").
- Status & Peripherals (barometer/current): read from a STATE text file; only
  the required fields are extracted; sensible defaults if missing.

Wire payload formats (little-endian, version=1)
- WireLoRa_t:     <B H h f B 64s  => v, pkt_count, rssi, snr, len, data[64]
- Wire433_t:      <B H h B 64s     => v, pkt_count, rssi, len, data[64]
- WireBarometer_t:<B I f f f       => v, ts_ms, P_hPa, T_C, Alt_m
- WireCurrent_t:  <B I f f f h     => v, ts_ms, I_A, V_V, P_W, raw_adc
- WireStatus_t:   <B I B B H H I I B
                  => v, uptime_s, system_state, flags, pc_lora, pc_433,
                     wakeup_time, free_heap, chip_revision

Framing:
    HELLO(0x7E) | PERIPHERAL_ID | LENGTH | PAYLOAD | GOODBYE(0x7F)

Usage:
    python3 simulate_embedded.py \
        --flight-log "/path/to/goanna flight log" \
        --state-file "/path/to/STATE" \
        [--baud 115200] [--device /dev/ttyUSB9] [--rate-hz 5] [--port-file sim_port.txt]

If --device is omitted, a PTY will be created and the slave path printed AND saved
to --port-file (default: sim_port.txt) so communicator.py can read it.
"""

import os
import re
import time
import pty
import tty
import struct
import logging
import argparse
from pathlib import Path

# ----------------------------
# Protocol constants
# ----------------------------
HELLO_BYTE   = 0x7E
GOODBYE_BYTE = 0x7F

PERIPHERAL_ID_SYSTEM     = 0x00
PERIPHERAL_ID_LORA_915   = 0x01
PERIPHERAL_ID_RADIO_433  = 0x02
PERIPHERAL_ID_BAROMETER  = 0x03
PERIPHERAL_ID_CURRENT    = 0x04

# Wire sizes (sanity)
import struct as _st
WIRE_LORA_SIZE   = _st.calcsize("<B H h f B 64s")     # 74
WIRE_433_SIZE    = _st.calcsize("<B H h B 64s")       # 70
WIRE_BARO_SIZE   = _st.calcsize("<B I f f f")         # 17
WIRE_CURR_SIZE   = _st.calcsize("<B I f f f h")       # 19
WIRE_STATUS_SIZE = _st.calcsize("<B I B B H H I I B") # 20

# Logging
logging.basicConfig(
    level=os.getenv("SIM_LOGLEVEL", "INFO"),
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sim-embedded")


# -----------------------------------------------------------------------------
# UInt32 helpers (avoid overflow on struct.pack '<I')
# -----------------------------------------------------------------------------
def u32(x: int) -> int:
    """Clamp to uint32 range (0..2^32-1)."""
    return int(x) & 0xFFFFFFFF

def now_u32_ms() -> int:
    """Return a ms timestamp masked to uint32 (safe for 'I' in struct.pack)."""
    return u32(int(time.time() * 1000))


# -----------------------------------------------------------------------------
# Helpers: parsing files
# -----------------------------------------------------------------------------
def read_lines_loop(path: Path):
    """
    Generator that yields lines from a file indefinitely.
    When EOF is reached, it rewinds to the beginning.
    """
    while True:
        with path.open("r", errors="ignore") as f:
            for line in f:
                yield line.rstrip("\n")
        time.sleep(0.2)  # tiny breather before looping

RSSI_RE = re.compile(r"RSSI\s*:\s*(-?\d+)")
SNR_RE  = re.compile(r"SNR\s*:\s*(-?\d+(\.\d+)?)")

def parse_log_line(line: str):
    """
    Parse a flight-log line into (rssi:int, snr:float, raw_payload_bytes:bytes).
    Missing fields are defaulted. Raw payload = text after '|' if present, else entire line.
    """
    rssi = -100
    snr  = 0.0
    m = RSSI_RE.search(line)
    if m:
        try: rssi = int(m.group(1))
        except Exception: pass
    m = SNR_RE.search(line)
    if m:
        try: snr = float(m.group(1))
        except Exception: pass

    raw = line.split("|", 1)[1].strip() if "|" in line else line.strip()
    raw_b = raw.encode("utf-8", errors="replace")[:64]  # truncate; padding in packers
    return rssi, snr, raw_b

def parse_state(path: Path):
    """
    Parse the STATE file into a dict of values we care about.
    Accepts loose "key: value" formats. Missing keys default later.
    """
    text = path.read_text(errors="ignore")
    kv = {}

    def grab(key, pattern, cast):
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            try: kv[key] = cast(m.group(1))
            except Exception: pass

    # Numerics
    grab("uptime_seconds",      r"uptime[_\s]*seconds\s*[:=]\s*(\d+)", int)
    grab("system_state",        r"system[_\s]*state\s*[:=]\s*(\d+)", int)
    grab("wakeup_time",         r"wakeup[_\s]*time\s*[:=]\s*(\d+)", int)
    grab("free_heap",           r"free[_\s]*heap\s*[:=]\s*(\d+)", int)
    grab("chip_revision",       r"chip[_\s]*revision\s*[:=]\s*(\d+)", int)
    grab("packet_count_lora",   r"packet[_\s]*count[_\s]*lora\s*[:=]\s*(\d+)", int)
    grab("packet_count_433",    r"packet[_\s]*count[_\s]*433\s*[:=]\s*(\d+)", int)

    # Bool-ish flags
    def grab_bool(key, pattern):
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            val = m.group(1).strip().lower()
            kv[key] = (val in ("1","true","yes","on","online","up","connected"))
    grab_bool("lora_online",        r"lora[_\s]*online\s*[:=]\s*([A-Za-z0-9]+)")
    grab_bool("radio433_online",    r"(?:433|radio433)[_\s]*online\s*[:=]\s*([A-Za-z0-9]+)")
    grab_bool("barometer_online",   r"baro(?:meter)?[_\s]*online\s*[:=]\s*([A-Za-z0-9]+)")
    grab_bool("current_online",     r"current[_\s]*online\s*[:=]\s*([A-Za-z0-9]+)")
    grab_bool("pi_connected",       r"pi[_\s]*connected\s*[:=]\s*([A-Za-z0-9]+)")

    # Barometer
    grab("baro_pressure_hpa",   r"pressure[_\s]*hpa\s*[:=]\s*([0-9.\-]+)", float)
    grab("baro_temperature_c",  r"temperature[_\s]*c\s*[:=]\s*([0-9.\-]+)", float)
    grab("baro_altitude_m",     r"altitude[_\s]*m\s*[:=]\s*([0-9.\-]+)", float)

    # Current/Power
    grab("current_a",           r"current[_\s]*a\s*[:=]\s*([0-9.\-]+)", float)
    grab("voltage_v",           r"voltage[_\s]*v\s*[:=]\s*([0-9.\-]+)", float)
    grab("power_w",             r"power[_\s]*w\s*[:=]\s*([0-9.\-]+)", float)
    grab("adc_raw",             r"(?:adc|raw[_\s]*adc)\s*[:=]\s*([\-]?\d+)", int)

    return kv


# -----------------------------------------------------------------------------
# Wire packers (exact struct layouts)
# -----------------------------------------------------------------------------
def pack_wire_lora(pkt_count: int, rssi_dbm: int, snr_db: float, latest: bytes) -> bytes:
    latest_len = min(len(latest), 64)
    latest_padded = latest.ljust(64, b"\x00")
    return struct.pack("<B H h f B 64s", 1, pkt_count & 0xFFFF, int(rssi_dbm), float(snr_db), latest_len, latest_padded)

def pack_wire_433(pkt_count: int, rssi_dbm: int, latest: bytes) -> bytes:
    latest_len = min(len(latest), 64)
    latest_padded = latest.ljust(64, b"\x00")
    return struct.pack("<B H h B 64s", 1, pkt_count & 0xFFFF, int(rssi_dbm), latest_len, latest_padded)

def pack_wire_barometer(ts_ms: int, pressure_hpa: float, temp_c: float, alt_m: float) -> bytes:
    return struct.pack("<B I f f f", 1, u32(ts_ms), float(pressure_hpa), float(temp_c), float(alt_m))

def pack_wire_current(ts_ms: int, current_a: float, voltage_v: float, power_w: float, raw_adc: int) -> bytes:
    return struct.pack("<B I f f f h", 1, u32(ts_ms), float(current_a), float(voltage_v), float(power_w), int(raw_adc))

def _flags_byte(lora: bool, r433: bool, baro: bool, curr: bool, pi: bool) -> int:
    b = 0
    b |= 0x01 if lora else 0
    b |= 0x02 if r433 else 0
    b |= 0x04 if baro else 0
    b |= 0x08 if curr else 0
    b |= 0x10 if pi else 0
    return b & 0xFF

def pack_wire_status(uptime_s: int, system_state: int, flags_b: int,
                     pkt_lora: int, pkt_433: int, wakeup_time: int,
                     free_heap: int, chip_rev: int) -> bytes:
    return struct.pack("<B I B B H H I I B",
                       1,
                       u32(uptime_s),
                       int(system_state) & 0xFF,
                       int(flags_b) & 0xFF,
                       int(pkt_lora) & 0xFFFF,
                       int(pkt_433) & 0xFFFF,
                       u32(wakeup_time),
                       u32(free_heap),
                       int(chip_rev) & 0xFF)


# -----------------------------------------------------------------------------
# Framing writer
# -----------------------------------------------------------------------------
def frame(peripheral_id: int, payload: bytes) -> bytes:
    """Build HELLO | PERIPHERAL_ID | LENGTH | PAYLOAD | GOODBYE (LENGTH <= 255)."""
    if len(payload) > 255:
        raise ValueError("Payload too long (>255) for 1-byte LENGTH")
    return bytes([HELLO_BYTE, peripheral_id, len(payload)]) + payload + bytes([GOODBYE_BYTE])


# -----------------------------------------------------------------------------
# Serial/PTY setup + safe write
# -----------------------------------------------------------------------------
def open_pty_or_device(device: str | None, baud: int):
    """
    If device is None: create a PTY pair and return (master_fd, slave_path).
    If device is provided: open it for writing and return (fd, device).
    Notes:
      - In PTY mode, communicator.py should open the *slave* path we print/save.
      - We write to the PTY *master* fd.
    """
    if device:
        fd = os.open(device, os.O_WRONLY | os.O_NOCTTY)  # blocking write
        return fd, device
    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)
    # Put master in raw mode (blocking). DO NOT set O_NONBLOCK to avoid EAGAIN.
    tty.setraw(master_fd)
    return master_fd, slave_path

def safe_write(fd: int, data: bytes, retries: int = 50, sleep_s: float = 0.01):
    """
    Write all bytes to fd with simple retry/backoff if OS reports EAGAIN.
    Blocking fds shouldn't need this, but it guards rare transient conditions.
    """
    total = 0
    n = len(data)
    while total < n:
        try:
            written = os.write(fd, data[total:])
            if written <= 0:
                raise RuntimeError("write returned 0; peer closed?")
            total += written
        except BlockingIOError:
            if retries <= 0:
                raise
            time.sleep(sleep_s)
            retries -= 1


# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Embedded protocol simulator")
    ap.add_argument("--flight-log", required=True, help="Path to 'goanna flight log' file")
    ap.add_argument("--state-file",  required=True, help="Path to STATE file")
    ap.add_argument("--device",      default=None, help="Existing serial device to write to; if omitted a PTY is created")
    ap.add_argument("--baud",        type=int, default=115200, help="Baud (for info only when using PTY)")
    ap.add_argument("--rate-hz",     type=float, default=5.0, help="Packets per second per channel (approx)")
    ap.add_argument("--baro-period", type=int, default=10, help="Emit barometer every N ticks")
    ap.add_argument("--curr-period", type=int, default=10, help="Emit current every N ticks")
    ap.add_argument("--status-period", type=int, default=5, help="Emit status every N ticks")
    ap.add_argument("--port-file",   default=str(Path(__file__).with_name("sim_port.txt")),
                    help="File to write the PTY slave path to (ignored if --device is set)")
    args = ap.parse_args()

    log_path = Path(args.flight_log)
    state_path = Path(args.state_file)
    if not log_path.exists():
        raise FileNotFoundError(f"flight log not found: {log_path}")
    if not state_path.exists():
        raise FileNotFoundError(f"STATE file not found: {state_path}")

    fd, slave_or_dev = open_pty_or_device(args.device, args.baud)
    if args.device:
        log.info("Writing frames to device: %s (baud=%d)", slave_or_dev, args.baud)
    else:
        # Save the slave path so communicator/run_all can read it
        port_file = Path(args.port_file)
        port_file.write_text(slave_or_dev + "\n", encoding="utf-8")
        log.info("Created PTY. Communicator should use --port %s (baud=%d)", slave_or_dev, args.baud)
        log.info("Saved port path to: %s", port_file)

    # Generators & counters
    lines = read_lines_loop(log_path)
    pkt_lora = 0
    pkt_433  = 0
    tick = 0
    last_state = {}

    # Timing
    period = 1.0 / max(args.rate_hz, 0.5)

    try:
        while True:
            tick += 1

            # Lora & 433 from the same flight-log line
            line = next(lines)
            rssi, snr, raw = parse_log_line(line)

            # LoRa 915
            pkt_lora += 1
            lora_payload = pack_wire_lora(pkt_lora, rssi_dbm=rssi, snr_db=snr, latest=raw)
            lora_frame = frame(PERIPHERAL_ID_LORA_915, lora_payload)
            safe_write(fd, lora_frame)

            # 433 (no SNR field)
            pkt_433 += 1
            rssi_433 = rssi + 2  # tiny variation
            radio_payload = pack_wire_433(pkt_433, rssi_dbm=rssi_433, latest=raw)
            radio_frame = frame(PERIPHERAL_ID_RADIO_433, radio_payload)
            safe_write(fd, radio_frame)

            # Refresh STATE periodically
            if tick % max(args.status_period, 1) == 0 or not last_state:
                last_state = parse_state(state_path)

            now_ms = now_u32_ms()

            # Barometer
            if args.baro_period > 0 and (tick % args.baro_period == 0):
                p = float(last_state.get("baro_pressure_hpa", 1013.25))
                t = float(last_state.get("baro_temperature_c", 22.0))
                a = float(last_state.get("baro_altitude_m", 50.0))
                baro_payload = pack_wire_barometer(now_ms, p, t, a)
                baro_frame = frame(PERIPHERAL_ID_BAROMETER, baro_payload)
                safe_write(fd, baro_frame)

            # Current/Power
            if args.curr_period > 0 and (tick % args.curr_period == 0):
                ia = float(last_state.get("current_a", 0.50))
                vv = float(last_state.get("voltage_v", 12.30))
                pw = float(last_state.get("power_w", ia * vv))
                adc = int(last_state.get("adc_raw", 512))
                curr_payload = pack_wire_current(now_ms, ia, vv, pw, adc)
                curr_frame = frame(PERIPHERAL_ID_CURRENT, curr_payload)
                safe_write(fd, curr_frame)

            # Status
            if args.status_period > 0 and (tick % args.status_period == 0):
                uptime = int(last_state.get("uptime_seconds", now_ms // 1000))
                state  = int(last_state.get("system_state", 1))
                flags_b = _flags_byte(
                    bool(last_state.get("lora_online", True)),
                    bool(last_state.get("radio433_online", True)),
                    bool(last_state.get("barometer_online", True)),
                    bool(last_state.get("current_online", True)),
                    bool(last_state.get("pi_connected", True)),
                )
                wake = int(last_state.get("wakeup_time", 0))
                heap = int(last_state.get("free_heap", 180000))
                rev  = int(last_state.get("chip_revision", 1))
                pc_lora = int(last_state.get("packet_count_lora", pkt_lora))
                pc_433  = int(last_state.get("packet_count_433", pkt_433))

                status_payload = pack_wire_status(uptime, state, flags_b, pc_lora, pc_433, wake, heap, rev)
                status_frame = frame(PERIPHERAL_ID_SYSTEM, status_payload)
                safe_write(fd, status_frame)

            time.sleep(period)

    except KeyboardInterrupt:
        log.info("Stopping simulator.")
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


if __name__ == "__main__":
    main()
