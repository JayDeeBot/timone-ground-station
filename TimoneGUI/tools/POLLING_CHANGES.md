# Polling Interval Changes - Summary for C++ Port

## Overview
Reduced the polling interval in test_sender.py to allow faster packet reception from ESP32.

## Changes Made to test_sender.py

### Location: Line 659
**Before:**
```python
time.sleep(0.05)  # 50ms polling interval
```

**After:**
```python
time.sleep(0.01)  # 10ms polling interval (allows up to ~100 packets/second)
```

### Impact:
- **Old rate**: Maximum ~20 packets/second (50ms between checks)
- **New rate**: Maximum ~100 packets/second (10ms between checks)
- **Baud limit**: Still limited by 115200 baud = ~11KB/s theoretical max

---

## Architecture Differences

### test_sender.py (Python GUI Test Tool)
- **Architecture**: Continuous polling loop in background thread
- **Polling method**: Checks `ser.in_waiting > 0` every 10ms
- **When to sleep**: Only sleeps when no data is available
- **Serial timeout**: `READ_TIMEOUT_S = 0.5s` (per read call)
- **Frame timeout**: `RX_TOTAL_TIMEOUT_S = 1.0s` (whole frame)

### communicator.py (Python Production Daemon)
- **Architecture**: Blocking read with deadline
- **Polling method**: `read_frame_blocking(timeout_s)` - waits for complete frame
- **When to sleep**: Only main thread sleeps (0.5s), serial read is blocking
- **Serial timeout**: `SERIAL_TIMEOUT_S = 0.2s` (per ser.read() call)
- **Frame timeout**: `REPLY_TIMEOUT_S = 1.2s` (whole frame)
- **Key difference**: Uses PySerial's built-in blocking reads, not polling loop

---

## Notes for C++ Port (Future Work)

### test_sender.py Architecture (THE MODEL TO FOLLOW):

**Single Reader Pattern:**
```python
# ONE thread reads ALL data (line 621)
def _continuous_reader(self):
    while running:
        if self.client.ser.in_waiting > 0:
            pid, payload = self.client.recv_frame()
            # Process frame (display, publish, route, etc.)
            # Update flags like pending_response
        else:
            time.sleep(0.01)  # 10ms poll

# Send thread ONLY writes, never reads (line 745)
def _send_thread(self, ...):
    self.pending_response = True
    self.client.send_command(...)  # Just write and return!
    # Reader thread will catch the reply
```

**Key points:**
- Only reader thread calls `recv_frame()`
- Send operations just write and set flags
- No race condition possible
- Works for both command replies AND autonomous data

### Current Python communicator.py problem:
1. RX thread reads frames (line 417)
2. CMD thread ALSO reads frames (line 534) ❌
3. **Race condition**: Both compete for serial data

### Recommended C++ approach (based on test_sender.py):

**Use the same single-reader pattern as test_sender.py:**

```cpp
// Single reader thread - reads ALL data
void serial_reader_thread() {
    while (!stop) {
        // Check if data available (non-blocking check like test_sender.py:626)
        if (serial.available() > 0) {
            Frame frame = recv_frame();

            // Process frame based on type
            if (frame.peripheral_id == PERIPHERAL_BAROMETER) {
                // Autonomous data - publish to ZMQ/GUI
                publish_telemetry(frame);
            } else if (frame.peripheral_id == PERIPHERAL_SYSTEM) {
                // Could be command reply or status - publish it
                publish_telemetry(frame);
            }
            // ... handle other peripherals

            // Clear any pending command flags if needed
            pending_response = false;
            last_rx_time = now();
        } else {
            // No data - sleep briefly like test_sender.py:659
            usleep(10000);  // 10ms = 100Hz polling
        }
    }
}

// Command sender - ONLY writes, never reads
void send_command(uint8_t peripheral_id, uint8_t cmd, const uint8_t* data, size_t len) {
    pending_response = true;

    // Build and send frame
    uint8_t frame[256];
    frame[0] = HELLO_BYTE;
    frame[1] = peripheral_id;
    frame[2] = len + 1;  // command + data
    frame[3] = cmd;
    memcpy(&frame[4], data, len);
    frame[4 + len] = GOODBYE_BYTE;

    serial.write(frame, 5 + len);

    // Don't wait for reply here! Reader thread will catch it.
}
```

**Alternative: Use select() instead of sleep (more efficient):**
```cpp
void serial_reader_thread() {
    while (!stop) {
        fd_set readfds;
        FD_ZERO(&readfds);
        FD_SET(serial_fd, &readfds);

        struct timeval tv = {.tv_sec = 0, .tv_usec = 10000};  // 10ms
        int ret = select(serial_fd + 1, &readfds, NULL, NULL, &tv);

        if (ret > 0 && FD_ISSET(serial_fd, &readfds)) {
            // Data available - process it
            Frame frame = recv_frame();
            publish_telemetry(frame);
        }
        // select() handles the 10ms wait - no explicit sleep needed
    }
}
```

### Key Takeaway:
- **Python test_sender.py**: Single reader thread with 10ms polling - THIS IS THE MODEL
- **Python communicator.py**: Has race condition (two threads reading) - NEEDS FIX
- **C++ version**: Should follow test_sender.py pattern (single reader, separate writers)
- **Effective timeout**: 10ms polling allows up to ~100 packets/second reception

---

## TODO: Fix communicator.py Race Condition

**Problem:** communicator.py has CMD thread reading serial (line 534), which races with RX thread.

**Solution:** Follow test_sender.py pattern - only RX thread reads, CMD thread uses a reply queue:

```python
class Communicator:
    def __init__(self):
        # ... existing code ...
        self.pending_replies = {}  # {peripheral_id: queue.Queue()}
        self._reply_lock = threading.Lock()

    def _rx_loop(self):
        """Single reader - handles ALL incoming frames"""
        while not self._stop.is_set():
            try:
                with self._ser_lock:
                    if self._ser is None or not self._ser.is_open:
                        self._connect_serial()

                # Read frame (only place that reads!)
                frame = self._framer.read_frame_blocking(timeout_s=0.2)
                if frame is None:
                    continue

                # Check if CMD thread is waiting for this peripheral's reply
                with self._reply_lock:
                    if frame.peripheral_id in self.pending_replies:
                        # Route to waiting CMD thread
                        q = self.pending_replies.pop(frame.peripheral_id)
                        q.put(frame)
                        continue  # Don't publish command replies

                # Normal autonomous data - decode and publish
                decoded = decode_wire_payload(frame.peripheral_id, frame.payload)
                self._publish(decoded["type"], decoded)

    def _roundtrip(self, peripheral_id, payload):
        """Send command and wait for reply via queue (not by reading!)"""
        # Register that we're expecting a reply
        reply_queue = queue.Queue()
        with self._reply_lock:
            self.pending_replies[peripheral_id] = reply_queue

        # Send command (only write, never read)
        with self._ser_lock:
            if not self._framer:
                raise RuntimeError("Serial not connected")
            self._framer.write_frame(peripheral_id, payload)

        # Wait for RX thread to route reply to our queue
        try:
            frame = reply_queue.get(timeout=REPLY_TIMEOUT_S)
            decoded = decode_wire_payload(frame.peripheral_id, frame.payload)
            return {
                "peripheral_id": frame.peripheral_id,
                "type": decoded.get("type", "raw"),
                "decoded": decoded["decoded"],
                "data": decoded["data"],
            }
        except queue.Empty:
            # Cleanup on timeout
            with self._reply_lock:
                self.pending_replies.pop(peripheral_id, None)
            raise TimeoutError("No reply from device")
```

This change makes communicator.py match test_sender.py's single-reader architecture.

---

## Testing Recommendations

With 10ms polling in test_sender.py, you can now test:
1. ESP32 autonomous polling at 20-50ms intervals (20-50 packets/second)
2. Burst transmissions from ESP32
3. Maximum throughput at 115200 baud

If you see issues, check:
- CPU usage (should be <5% for the reader thread)
- Missed packets (check throughput.csv and polling statistics)
- Consider increasing baud rate to 921600 if Pi/ESP32 support it
