#!/usr/bin/env python3
"""
Byte-Stuffed Protocol Implementation

Frame Format:
[START_MARKER (2 bytes)][PID][LENGTH][stuffed_payload][CHECKSUM][END_MARKER (2 bytes)]

Frame markers: 0xAA 0x55 (start), 0x55 0xAA (end)
Escape byte: 0xDB

Escaping rules (only these 3 bytes need escaping):
- 0xAA in payload → 0xDB 0xAC
- 0x55 in payload → 0xDB 0x57
- 0xDB in payload → 0xDB 0xDD

All other bytes (including old 0x7D, 0x7E, 0x7F) pass through unchanged!
"""

# Protocol constants
START_MARKER = bytes([0xAA, 0x55])
END_MARKER = bytes([0x55, 0xAA])
ESCAPE_BYTE = 0xDB

# Escape mappings
ESCAPE_MAP = {
    0xAA: 0xAC,  # Start marker byte 1
    0x55: 0x57,  # Start marker byte 2 / End marker byte 1
    0xDB: 0xDD,  # Escape byte itself
}

# Reverse mapping for decoding
UNESCAPE_MAP = {v: k for k, v in ESCAPE_MAP.items()}


def stuff_payload(data: bytes) -> bytes:
    """
    Apply byte stuffing to payload.

    Only escapes 0xAA, 0x55, 0xDB. All other bytes pass through.

    Args:
        data: Raw payload bytes

    Returns:
        Stuffed payload (may be longer if special bytes present)

    Example:
        >>> stuff_payload(bytes([0x01, 0xAA, 0x03]))
        b'\\x01\\xdb\\xac\\x03'
    """
    result = bytearray()

    for byte in data:
        if byte in ESCAPE_MAP:
            # Special byte - escape it
            result.append(ESCAPE_BYTE)
            result.append(ESCAPE_MAP[byte])
        else:
            # Normal byte (including 0x7D!)
            result.append(byte)

    return bytes(result)


def unstuff_payload(data: bytes) -> bytes:
    """
    Remove byte stuffing from received payload.

    Args:
        data: Stuffed payload bytes

    Returns:
        Original unstuffed payload

    Raises:
        ValueError: If stuffing is malformed

    Example:
        >>> unstuff_payload(bytes([0x01, 0xDB, 0xAC, 0x03]))
        b'\\x01\\xaa\\x03'
    """
    result = bytearray()
    i = 0

    while i < len(data):
        if data[i] == ESCAPE_BYTE:
            # Escape sequence
            if i + 1 >= len(data):
                raise ValueError("Incomplete escape sequence at end of data")

            escaped_byte = data[i + 1]
            if escaped_byte in UNESCAPE_MAP:
                # Valid escape - decode it
                result.append(UNESCAPE_MAP[escaped_byte])
                i += 2  # Skip both escape byte and escaped value
            else:
                raise ValueError(f"Invalid escape sequence: 0xDB 0x{escaped_byte:02X}")
        else:
            # Normal byte (not escaped)
            result.append(data[i])
            i += 1

    return bytes(result)


def calculate_checksum(data: bytes) -> int:
    """
    Calculate simple XOR checksum.

    Args:
        data: Payload bytes (before stuffing)

    Returns:
        8-bit checksum value
    """
    checksum = 0
    for byte in data:
        checksum ^= byte
    return checksum & 0xFF


def encode_frame(peripheral_id: int, payload: bytes) -> bytes:
    """
    Encode a complete frame with byte stuffing.

    Args:
        peripheral_id: Peripheral ID (0-255)
        payload: Raw payload data

    Returns:
        Complete frame ready to send over serial

    Frame structure:
        [0xAA 0x55][PID][LEN][stuffed_payload][CHKSUM][0x55 0xAA]
    """
    # Calculate checksum of original payload
    checksum = calculate_checksum(payload)

    # Stuff the payload
    stuffed = stuff_payload(payload)

    # Build frame
    frame = bytearray()
    frame.extend(START_MARKER)          # 0xAA 0x55
    frame.append(peripheral_id)          # PID
    frame.append(len(stuffed) & 0xFF)   # Stuffed length (may be > original)
    frame.extend(stuffed)                # Stuffed payload
    frame.append(checksum)               # Checksum of original payload
    frame.extend(END_MARKER)             # 0x55 0xAA

    return bytes(frame)


def decode_frame(ser, verbose=True) -> tuple:
    """
    Decode a frame from serial port with byte stuffing.

    Args:
        ser: Serial port object
        verbose: Print debug messages

    Returns:
        (peripheral_id, payload) tuple

    Raises:
        TimeoutError: If frame not received in time
        ValueError: If frame is malformed or checksum fails
    """
    import time

    def log(msg):
        if verbose:
            print(msg)

    start_time = time.time()
    timeout = 2.0

    # 1. Find START_MARKER (0xAA 0x55)
    log("[DEBUG] Searching for START_MARKER (0xAA 0x55)...")
    skipped = 0
    while time.time() - start_time < timeout:
        b1 = ser.read(1)
        if not b1:
            continue

        if b1[0] == START_MARKER[0]:  # Found 0xAA
            # Check if next byte is 0x55
            b2 = ser.read(1)
            if b2 and b2[0] == START_MARKER[1]:  # Found 0x55
                if skipped > 0:
                    log(f"[WARNING] Skipped {skipped} bytes before START_MARKER")
                log("[DEBUG] Found START_MARKER")
                break
            elif b2:
                # False alarm, keep searching
                skipped += 2
        else:
            skipped += 1
    else:
        raise TimeoutError("Timed out waiting for START_MARKER (0xAA 0x55)")

    # 2. Read header (PID + LENGTH)
    hdr = ser.read(2)
    if len(hdr) != 2:
        raise TimeoutError("Timed out reading header (PID, LENGTH)")

    pid = hdr[0]
    stuffed_length = hdr[1]
    log(f"[DEBUG] PID=0x{pid:02X}, Stuffed Length={stuffed_length}")

    # 3. Read stuffed payload
    stuffed_payload = b""
    bytes_remaining = stuffed_length
    read_deadline = time.time() + 2.0

    while bytes_remaining > 0 and time.time() < read_deadline:
        chunk = ser.read(bytes_remaining)
        if chunk:
            stuffed_payload += chunk
            bytes_remaining -= len(chunk)
        else:
            time.sleep(0.01)

    if len(stuffed_payload) != stuffed_length:
        raise TimeoutError(f"Partial payload: expected {stuffed_length}, got {len(stuffed_payload)}")

    log(f"[DEBUG] Read {len(stuffed_payload)} stuffed payload bytes")

    # 4. Read checksum
    chk = ser.read(1)
    if len(chk) != 1:
        raise TimeoutError("Timed out reading checksum")
    received_checksum = chk[0]

    # 5. Read END_MARKER (0x55 0xAA)
    end = ser.read(2)
    if len(end) != 2 or end != END_MARKER:
        if end:
            raise ValueError(f"Invalid END_MARKER: expected 0x55AA, got 0x{end.hex()}")
        else:
            raise ValueError("Missing END_MARKER")

    log("[DEBUG] Found END_MARKER (0x55 0xAA)")

    # 6. Unstuff payload
    try:
        payload = unstuff_payload(stuffed_payload)
        log(f"[DEBUG] Unstuffed to {len(payload)} original bytes")
    except ValueError as e:
        raise ValueError(f"Unstuffing failed: {e}")

    # 7. Verify checksum
    calculated_checksum = calculate_checksum(payload)
    if calculated_checksum != received_checksum:
        raise ValueError(f"Checksum mismatch: calculated 0x{calculated_checksum:02X}, received 0x{received_checksum:02X}")

    log("[DEBUG] Checksum valid ✓")

    return pid, payload


# =============================================================================
# Testing and Examples
# =============================================================================

def test_stuffing():
    """Test byte stuffing with various cases"""
    print("="*60)
    print("Testing Byte Stuffing")
    print("="*60)

    test_cases = [
        ("No special bytes", bytes([0x01, 0x02, 0x03, 0x04])),
        ("Contains 0x7D (old problem)", bytes([0x01, 0x7D, 0x03, 0x04])),
        ("Contains 0xAA (start marker)", bytes([0x01, 0xAA, 0x03, 0x04])),
        ("Contains 0x55 (end marker)", bytes([0x01, 0x55, 0x03, 0x04])),
        ("Contains 0xDB (escape byte)", bytes([0x01, 0xDB, 0x03, 0x04])),
        ("Contains all specials", bytes([0xAA, 0x55, 0xDB])),
        ("Barometer with 0x7D", bytes([0x01, 0xF8, 0x0A, 0x2B, 0x00, 0x50, 0x7D, 0x44])),
    ]

    for name, original in test_cases:
        stuffed = stuff_payload(original)
        unstuffed = unstuff_payload(stuffed)

        print(f"\n{name}:")
        print(f"  Original:  {original.hex()}")
        print(f"  Stuffed:   {stuffed.hex()}")
        print(f"  Unstuffed: {unstuffed.hex()}")

        assert original == unstuffed, f"FAILED: {name}"

        if len(stuffed) > len(original):
            print(f"  Overhead: +{len(stuffed) - len(original)} bytes")
        else:
            print(f"  Overhead: none")

    print("\n" + "="*60)
    print("All tests passed! ✓")
    print("="*60)


def show_frame_example():
    """Show example of encoded frame"""
    print("\n" + "="*60)
    print("Example Frame Encoding")
    print("="*60)

    # Barometer data with 0x7D in it (pressure = 1013.0)
    payload = bytes([
        0x01,                      # version
        0xF8, 0x0A, 0x2B, 0x00,   # timestamp
        0x00, 0x50, 0x7D, 0x44,   # pressure (contains 0x7D!)
        0x00, 0x00, 0xB4, 0x41,   # temperature
        0xCD, 0xCC, 0xF6, 0x42,   # altitude
    ])

    frame = encode_frame(0x03, payload)  # PID=0x03 (barometer)

    print(f"\nOriginal payload ({len(payload)} bytes):")
    print(f"  {payload.hex()}")
    print(f"  Note: byte 7 is 0x7D (from float 1013.0)")

    print(f"\nEncoded frame ({len(frame)} bytes):")
    print(f"  {frame.hex()}")

    print(f"\nFrame breakdown:")
    print(f"  START:   {frame[0:2].hex()} (0xAA 0x55)")
    print(f"  PID:     {frame[2]:02X} (barometer)")
    print(f"  LENGTH:  {frame[3]:02X} ({frame[3]} bytes stuffed)")
    print(f"  PAYLOAD: {frame[4:-3].hex()}")
    print(f"  CHKSUM:  {frame[-3]:02X}")
    print(f"  END:     {frame[-2:].hex()} (0x55 0xAA)")

    print(f"\nNote: 0x7D is NOT escaped (not in our escape list)")
    print(f"      Only 0xAA, 0x55, 0xDB are escaped")


if __name__ == "__main__":
    test_stuffing()
    show_frame_example()
