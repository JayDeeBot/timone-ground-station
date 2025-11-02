#!/usr/bin/env python3
"""
Raw Serial Monitor - Captures and displays raw bytes with frame boundaries

Helps diagnose serial desync issues by showing:
- Every byte received with timestamps
- Frame boundaries (RESPONSE=0x7D, GOODBYE=0x7F markers)
- Gaps/delays between bytes
- Buffer state
"""

import serial
import time
import sys

RESPONSE_BYTE = 0x7D
GOODBYE_BYTE = 0x7F
HELLO_BYTE = 0x7E

def monitor_serial(port, baudrate=115200, duration_sec=30):
    """Monitor raw serial data and mark frame boundaries"""

    print(f"Opening {port} at {baudrate} baud...")
    ser = serial.Serial(
        port=port,
        baudrate=baudrate,
        timeout=0.1,  # 100ms timeout per read
        xonxoff=False,
        rtscts=False,
        dsrdtr=False
    )

    print(f"Monitoring for {duration_sec} seconds...")
    print("=" * 80)
    print("Format: [timestamp] [in_frame] byte_hex (ascii) <markers>")
    print("=" * 80)

    start_time = time.time()
    byte_count = 0
    in_frame = False
    frame_bytes = []
    frame_start_time = None
    last_byte_time = start_time

    while time.time() - start_time < duration_sec:
        b = ser.read(1)
        if not b:
            # No data available
            time.sleep(0.001)  # 1ms
            continue

        byte_count += 1
        current_time = time.time()
        elapsed = current_time - start_time
        gap = current_time - last_byte_time
        last_byte_time = current_time

        byte_val = b[0]
        ascii_char = chr(byte_val) if 32 <= byte_val < 127 else '.'

        # Build output line
        marker = ""
        if byte_val == RESPONSE_BYTE:
            marker = " <RESPONSE_START>"
            if in_frame:
                marker += " [ERROR: Already in frame!]"
            in_frame = True
            frame_start_time = current_time
            frame_bytes = [byte_val]
        elif byte_val == GOODBYE_BYTE:
            marker = " <GOODBYE_END>"
            if not in_frame:
                marker += " [ERROR: Not in frame!]"
            else:
                frame_duration = current_time - frame_start_time
                marker += f" [Frame: {len(frame_bytes)} bytes in {frame_duration*1000:.1f}ms]"
            in_frame = False
            frame_bytes = []
        elif byte_val == HELLO_BYTE:
            marker = " <HELLO (unexpected in response)>"

        if in_frame:
            frame_bytes.append(byte_val)

        # Show gap if significant
        gap_marker = f" [gap: {gap*1000:.1f}ms]" if gap > 0.05 else ""

        # Print the line
        print(f"[{elapsed:7.3f}s] {'IN ' if in_frame else 'OUT'} 0x{byte_val:02X} ({ascii_char}){marker}{gap_marker}")

        # Show buffer state periodically
        if byte_count % 100 == 0:
            waiting = ser.in_waiting
            print(f"--- [{elapsed:.3f}s] Received {byte_count} bytes, {waiting} waiting in buffer ---")

    print("=" * 80)
    print(f"Monitoring complete: {byte_count} bytes received in {duration_sec} seconds")
    print(f"Average rate: {byte_count/duration_sec:.1f} bytes/sec")

    if in_frame:
        print(f"[WARNING] Still in frame at end ({len(frame_bytes)} bytes): {' '.join(f'{b:02X}' for b in frame_bytes[:20])}")

    ser.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 serial_raw_monitor.py /dev/ttyUSB0 [baudrate] [duration_sec]")
        print("Example: python3 serial_raw_monitor.py /dev/ttyUSB0 115200 30")
        sys.exit(1)

    port = sys.argv[1]
    baudrate = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
    duration = int(sys.argv[3]) if len(sys.argv) > 3 else 30

    try:
        monitor_serial(port, baudrate, duration)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback
        traceback.print_exc()
