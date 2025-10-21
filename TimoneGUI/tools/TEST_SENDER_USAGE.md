# test_sender.py - Usage Guide

## Overview

`test_sender.py` is a Tkinter GUI tool for testing communication with the ESP32 ground station using the **new peripheral-based protocol**. It allows you to send commands to specific peripherals and see decoded responses.

## What Changed - New Protocol

### OLD Protocol (Before):
- Commands sent to SYSTEM peripheral only
- Responses used HELLO_BYTE (0x7E) same as commands
- Commands like `CMD_GET_LORA_DATA` were system-level

### NEW Protocol (Current):
- Commands sent to **specific peripherals** (LORA_915, BAROMETER, etc.)
- Responses use **RESPONSE_BYTE (0x7D)** instead of HELLO_BYTE
- Generic commands (GET_ALL, GET_STATUS) work for **all peripherals**
- System commands (WAKEUP, SLEEP, RESET) only for SYSTEM peripheral

## Quick Start

### 1. Launch the GUI

```bash
python3 test_sender.py
```

### 2. Connect to ESP32

1. Select your serial port from the dropdown (e.g., `/dev/ttyACM0`)
2. Set baud rate (default: 115200)
3. Click **Connect**

### 3. Send Commands

**Basic workflow:**
1. Select a **Peripheral** (e.g., LORA_915, SYSTEM, BAROMETER)
2. Select a **Command** (e.g., GET_ALL, WAKEUP)
3. (Optional) Add extra data in hex format in "Payload" field
4. Click **Send**

## Common Operations

### Wake Up the ESP32

The ESP32 starts in low-power mode. To wake it up:

1. Peripheral: **SYSTEM**
2. Command: **WAKEUP**
3. Click **Send** (or click the quick **WAKEUP** button)

Expected response: `ACK command: 0x20`

### Get LoRa 915MHz Data

1. Peripheral: **LORA_915**
2. Command: **GET_ALL**
3. Click **Send** (or click the quick **GET_ALL** button)

Expected response: Decoded LoRa data with packet count, RSSI, SNR, payload

### Get Barometer Data

1. Peripheral: **BAROMETER**
2. Command: **GET_ALL**
3. Click **Send**

Expected response: Pressure, temperature, altitude

### Get Current Sensor Data

1. Peripheral: **CURRENT**
2. Command: **GET_ALL**
3. Click **Send**

Expected response: Current, voltage, power

### Get System Status

1. Peripheral: **SYSTEM**
2. Command: **GET_ALL**
3. Click **Send**

Expected response: Full system status (uptime, heap, packet counts, etc.)

## GUI Features

### Quick Action Buttons

- **WAKEUP** - Quickly send wakeup command to SYSTEM
- **GET_ALL** - Quickly get data from LORA_915

### Presets

- **Save Preset** - Save current peripheral, command, and payload configuration
- **Load Preset** - Load a previously saved configuration

Useful for repeated testing scenarios.

### Log Window

Shows all transmitted and received messages with:
- TX messages with peripheral and command info
- RX messages with hex dump
- Decoded payload (if recognized format)

## Peripheral Commands Reference

### Generic Commands (All Peripherals)

| Command | ID | Description |
|---------|-----|-------------|
| GET_ALL | 0x00 | Get all available data from peripheral |
| GET_STATUS | 0x01 | Get status/health of peripheral |
| RESET | 0x02 | Reset peripheral |
| CONFIGURE | 0x03 | Configure peripheral (future) |

### System Commands (SYSTEM Only)

| Command | ID | Description |
|---------|-----|-------------|
| WAKEUP | 0x20 | Wake up system from low-power state |
| SLEEP | 0x21 | Put system into low-power state |
| RESET_SYSTEM | 0x22 | Reset entire ESP32 |

## Message Format Details

### Command (Pi → ESP32)
```
[HELLO=0x7E][PERIPHERAL_ID][LENGTH][COMMAND][optional data...][GOODBYE=0x7F]
```

Example - Get LoRa data:
```
7E 01 01 00 7F
│  │  │  │  └─ GOODBYE
│  │  │  └──── CMD_GET_ALL (0x00)
│  │  └─────── LENGTH (1 byte)
│  └────────── LORA_915 (0x01)
└───────────── HELLO
```

### Response (ESP32 → Pi)
```
[RESPONSE=0x7D][PERIPHERAL_ID][LENGTH][data...][GOODBYE=0x7F]
```

Example - LoRa response:
```
7D 01 4A [74 bytes of WireLoRa_t data] 7F
│  │  │                                 └─ GOODBYE
│  │  └───────────────────────────────── LENGTH (74 bytes)
│  └──────────────────────────────────── LORA_915 (0x01)
└─────────────────────────────────────── RESPONSE
```

## Decoded Data Structures

The tool automatically decodes these binary structures:

### WireHeartbeat_t (6 bytes)
```python
{
    'version': int,
    'uptime_seconds': int,
    'system_state': int
}
```

### WireStatus_t (20 bytes)
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

## Example Test Sequence

1. **Connect** to serial port
2. **WAKEUP** - Wake up ESP32
   - Response: `ACK command: 0x20`
3. **Wait** ~1 second for system to initialize
4. **Get System Status** (SYSTEM → GET_ALL)
   - Response: Full status with uptime, heap, etc.
5. **Get LoRa Data** (LORA_915 → GET_ALL)
   - Response: LoRa packet data
6. **Get 433MHz Data** (LORA_433 → GET_ALL)
   - Response: 433MHz packet data
7. **Get Barometer** (BAROMETER → GET_ALL)
   - Response: Pressure, temp, altitude
8. **Get Current** (CURRENT → GET_ALL)
   - Response: Current, voltage, power

## Troubleshooting

### No Response Received

**Symptoms:**
```
RX ERROR: Timed out waiting for RESPONSE_BYTE (0x7D)
```

**Possible causes:**
- ESP32 not powered on
- Wrong serial port
- Wrong baud rate
- ESP32 in low-power mode (send WAKEUP first)
- ESP32 firmware not updated to new protocol

**Solutions:**
1. Check ESP32 power and connection
2. Try different serial ports
3. Verify baud rate matches ESP32 (default: 115200)
4. Send WAKEUP command first
5. Check ESP32 firmware version

### Unexpected Bytes in Log

**Symptoms:**
```
[DEBUG] Skipping byte: 0x?? (waiting for RESPONSE=0x7D)
```

**Cause:** ESP32 sending debug output or unsolicited messages

**Solution:** This is normal - the tool skips non-protocol bytes

### Decode Error

**Symptoms:**
```
Decoded: Decode error: Invalid status size: expected 20, got 15
```

**Cause:** Payload size doesn't match expected structure

**Solutions:**
- ESP32 firmware may be different version
- Peripheral may not be initialized
- Check ESP32 logs for errors

### Permission Denied

**Symptoms:**
```
PermissionError: [Errno 13] Permission denied: '/dev/ttyACM0'
```

**Solution:**
```bash
sudo chmod 666 /dev/ttyACM0
# Or add user to dialout group:
sudo usermod -a -G dialout $USER
# Then log out and back in
```

## Advanced Usage

### Sending Custom Payloads

You can add hex bytes to the "Payload (hex bytes)" field for commands that accept parameters.

Example - Send command with 4 bytes of data:
1. Select peripheral and command
2. Enter in payload field: `01 02 03 04`
3. Click Send

The frame will be:
```
[HELLO][PERIPHERAL_ID][LENGTH][COMMAND][01 02 03 04][GOODBYE]
```

### Creating Test Sequences

Use presets to create repeatable test sequences:

1. Configure first command
2. Save Preset → `test_sequence_1.json`
3. Configure second command
4. Save Preset → `test_sequence_2.json`
5. Later: Load each preset and send in sequence

## See Also

- [comm_hub.py](../src/scripts/comm_hub.py) - Production communication hub
- [COMM_HUB_USAGE.md](../src/scripts/COMM_HUB_USAGE.md) - Comm hub documentation
- ESP32 firmware source code (for protocol details)
