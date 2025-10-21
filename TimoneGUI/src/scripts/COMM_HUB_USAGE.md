# comm_hub.py - Usage Guide

## Overview

`comm_hub.py` is a Python script for communicating with the ESP32-based Timone ground station using the new peripheral-based protocol. It can:

- Send commands to specific peripherals (LoRa 915MHz, LoRa 433MHz, barometer, current sensor, system)
- Receive and decode responses from the ESP32
- Display formatted data from sensors
- Optionally poll sensors periodically
- Run in simulation mode for testing

## Protocol Summary

The ESP32 communication uses a modular peripheral-based protocol:

**Command Format (Pi → ESP32):**
```
[HELLO=0x7E][PERIPHERAL_ID][LENGTH][COMMAND][data...][GOODBYE=0x7F]
```

**Response Format (ESP32 → Pi):**
```
[RESPONSE=0x7D][PERIPHERAL_ID][LENGTH][data...][GOODBYE=0x7F]
```

### Peripheral IDs
- `0x00` - SYSTEM (ESP32 control)
- `0x01` - LORA_915 (915MHz LoRa)
- `0x02` - LORA_433 (433MHz LoRa)
- `0x03` - BAROMETER (MS5607)
- `0x04` - CURRENT (Current/voltage sensor)

### Generic Commands (all peripherals)
- `0x00` - CMD_GET_ALL (get all data)
- `0x01` - CMD_GET_STATUS (get status/health)
- `0x02` - CMD_RESET (reset peripheral)
- `0x03` - CMD_CONFIGURE (configure peripheral)

### System Commands (PERIPHERAL_ID=0x00 only)
- `0x20` - CMD_SYSTEM_WAKEUP (wake from low-power)
- `0x21` - CMD_SYSTEM_SLEEP (enter low-power)
- `0x22` - CMD_SYSTEM_RESET (reset ESP32)

## Basic Usage

### Connect and Listen for Unsolicited Messages

The ESP32 sends heartbeat/status messages automatically. To listen:

```bash
python3 comm_hub.py --port /dev/ttyACM0 -v
```

This will display all incoming messages from the ESP32.

### Wake Up the System

The ESP32 starts in a low-power waiting state. To wake it up:

```bash
python3 comm_hub.py --port /dev/ttyACM0 --wakeup -v
```

### Poll All Sensors Periodically

To request data from all sensors every 5 seconds:

```bash
python3 comm_hub.py --port /dev/ttyACM0 --wakeup --poll 5 -v
```

### Change Baud Rate

Default is 115200. To use a different baud rate:

```bash
python3 comm_hub.py --port /dev/ttyACM0 --baud 9600 -v
```

### Run in Simulation Mode (No Serial)

For testing without hardware:

```bash
python3 comm_hub.py --sim -v
```

## Command-Line Options

```
--port PORT              Serial port (e.g., /dev/ttyACM0)
--baud BAUD             Baud rate (default: 115200)
--tcp-433 TCP_433       TCP server for 433MHz GUI (optional)
--tcp-915 TCP_915       TCP server for 915MHz GUI (optional)
--tcp-settings TCP_SETTINGS  TCP server for settings GUI (optional)
--sim                   Run without serial for testing
--poll POLL             Poll sensors every N seconds (0=disabled)
--wakeup                Send wakeup command on startup
-v, --verbose           Increase verbosity (-v, -vv)
```

## Data Structures

The script automatically unpacks these binary data structures:

### WireHeartbeat_t (6 bytes)
Sent before wakeup, every 5 seconds:
```python
{
    'version': int,
    'uptime_seconds': int,
    'system_state': int
}
```

### WireStatus_t (20 bytes)
Sent after wakeup, every 5 seconds:
```python
{
    'version': int,
    'uptime_seconds': int,
    'system_state': int,
    'sensor_flags': int,
    'pkt_count_lora': int,
    'pkt_count_433': int,
    'wakeup_time': int,
    'heap_free': int,
    'chip_revision': int
}
```

### WireLoRa_t (74 bytes)
LoRa 915MHz and 433MHz data:
```python
{
    'version': int,
    'packet_count': int,
    'rssi': int,
    'snr': float,
    'payload_length': int,
    'payload': bytes
}
```

### WireBarometer_t (17 bytes)
Barometer data:
```python
{
    'version': int,
    'timestamp': int,
    'pressure_pa': float,
    'temperature_c': float,
    'altitude_m': float
}
```

### WireCurrent_t (19 bytes)
Current sensor data:
```python
{
    'version': int,
    'timestamp': int,
    'current_a': float,
    'voltage_v': float,
    'power_w': float,
    'raw_adc': int
}
```

## Example Output

When running with `--wakeup --poll 5 -v`:

```
2025-10-17 15:51:59 | INFO | Serial opened on /dev/ttyACM0 @ 115200
2025-10-17 15:51:59 | INFO | Sending wakeup command...
2025-10-17 15:51:59 | INFO | Sent system wakeup command
2025-10-17 15:52:00 | INFO | Received from SYSTEM (0x00): {'ack_command': '0x20'}
2025-10-17 15:52:04 | INFO | Received from SYSTEM (0x00): {'version': 1, 'uptime_seconds': 245, ...}
2025-10-17 15:52:05 | INFO | Received from LORA_915 (0x01): {'version': 1, 'packet_count': 42, 'rssi': -67, ...}
2025-10-17 15:52:05 | INFO | Received from LORA_433 (0x02): {'version': 1, 'packet_count': 18, 'rssi': -72, ...}
```

## GUI Client Integration (Optional)

The script can optionally run TCP servers for GUI clients:

```bash
python3 comm_hub.py --port /dev/ttyACM0 \
    --tcp-915 127.0.0.1:9402 \
    --tcp-433 127.0.0.1:9401 \
    --tcp-settings 127.0.0.1:9403 \
    --wakeup --poll 5 -v
```

GUI clients can connect and send JSON commands:

```json
{"command": "get_lora"}
{"command": "get_433"}
{"command": "get_barometer"}
{"command": "get_status"}
{"command": "wakeup"}
```

Or raw commands:
```json
{"peripheral_id": 1, "command_id": 0, "data_hex": ""}
```

## Troubleshooting

### No data received
- Ensure the ESP32 is powered on and connected
- Check the serial port is correct (`ls /dev/tty*`)
- Try sending wakeup: `--wakeup`
- Increase verbosity: `-vv`

### Permission denied on serial port
```bash
sudo chmod 666 /dev/ttyACM0
# Or add user to dialout group:
sudo usermod -a -G dialout $USER
# Then log out and back in
```

### Wrong baud rate
The ESP32 must be configured to use the same baud rate (default: 115200).

## Advanced Usage

### Custom Polling Sequence

To modify what gets polled, edit the `polling_task()` function in [comm_hub.py](comm_hub.py:559-584).

### Add New Peripherals

1. Add peripheral ID constant (e.g., `PERIPHERAL_ID_NEW_SENSOR = 0x05`)
2. Add to `PERIPHERAL_NAMES` dict
3. Add unpacking function (e.g., `unpack_new_sensor_data()`)
4. Add case in `Hub._unpack_payload()`

### Custom Commands

To send custom commands programmatically:

```python
import asyncio
from comm_hub import EmbeddedLink, FrameCodec

async def custom_test():
    codec = FrameCodec()
    link = EmbeddedLink(codec, "/dev/ttyACM0", 115200)
    await link.connect()

    # Send custom command
    await link.send_command(
        peripheral_id=0x01,  # LoRa 915
        command=0x00,        # CMD_GET_ALL
        data=b''             # No extra data
    )

    # Read response
    frames = await link.read_frames()
    for peripheral_id, payload in frames:
        print(f"Got response: {payload.hex()}")

asyncio.run(custom_test())
```

## See Also

- ESP32 firmware source code (for protocol details)
- [comm_hub.py](comm_hub.py) source code with inline documentation
