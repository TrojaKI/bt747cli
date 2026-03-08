"""MTK PMTK protocol: commands and PMTK182,8 data parsing.

The download flow uses the PMTK182 command set (confirmed against BT747 source,
MTKLogDownloadHandler.java + BT747Constants.java):

  1. $PMTK000          → wakeup ping; wait for $PMTK001,0,3
  2. $PMTK182,2,7      → query next-write-address (= bytes used in flash)
     Reply: $PMTK182,3,7,<hex_addr>
  3. $PMTK182,7,<start_hex8>,<len_hex8>  → request log chunk
     Reply: $PMTK182,8,<start_hex8>,<hexdata>
             then: $PMTK001,182,7,3  (success ACK)
  4. Repeat step 3 in 0x800-byte chunks (BT747 default) until fully downloaded.
     Never cross a 0x10000 boundary within one request (firmware limitation).

BT747Constants:
  PMTK_CMD_LOG     = 182
  PMTK_LOG_Q       = 2   (query parameter)
  PMTK_LOG_DT      = 3   (reply to query)
  PMTK_LOG_Q_LOG   = 7   (request log data chunk)
  PMTK_LOG_DT_LOG  = 8   (reply with log data)

PMTK001 ACK result codes: 0=invalid, 1=unsupported, 2=failed, 3=success
"""

from __future__ import annotations

import binascii
import logging
import time

from .connection import SerialConnection

log = logging.getLogger(__name__)

# Download chunk size – matches BT747 default logRequestStep.
CHUNK_SIZE = 0x800

# Number of seconds to wait for the device to finish sending all log data.
DOWNLOAD_TIMEOUT = 300.0
# Timeout for single query/response round-trips.
QUERY_TIMEOUT = 5.0


def _verify_sentence(sentence: str) -> bool:
    """Return True when the NMEA/PMTK checksum in *sentence* is valid."""
    if not sentence.startswith("$"):
        return False
    if "*" not in sentence:
        return False
    payload, cs_str = sentence[1:].rsplit("*", 1)
    cs_str = cs_str[:2]
    checksum = 0
    for ch in payload:
        checksum ^= ord(ch)
    return cs_str.upper() == f"{checksum:02X}"


def _wakeup(conn: SerialConnection) -> bool:
    """Send $PMTK000 (test command) and wait for the ACK.

    Returns True when the device responds, False on timeout.
    """
    log.debug("Sending wakeup ping ($PMTK000) …")
    conn.send_command("PMTK000")
    deadline = time.monotonic() + QUERY_TIMEOUT
    while time.monotonic() < deadline:
        line = conn.read_line()
        if not line:
            continue
        log.debug("  wakeup rx: %s", line)
        if "PMTK001,0" in line:
            log.debug("Device is awake.")
            return True
    log.warning("No wakeup ACK received – continuing anyway.")
    return False


def _query_log_size(conn: SerialConnection) -> int | None:
    """Query the next-write-address via $PMTK182,2,7.

    Returns the byte offset of the current write pointer (= where the device
    will write the next record).  When the flash has wrapped this value is
    SMALL (the write head is near the start of the flash again).  Use
    _query_flash_size() to determine how much flash to download in that case.
    Returns None if the query fails.
    """
    log.debug("Querying log write pointer ($PMTK182,2,7) …")
    conn.send_command("PMTK182,2,7")
    deadline = time.monotonic() + QUERY_TIMEOUT
    while time.monotonic() < deadline:
        line = conn.read_line()
        if not line:
            continue
        log.debug("rx: %s", line)
        if not line.startswith("$PMTK182,3,7"):
            continue
        # Format: $PMTK182,3,7,<hex_value>*XX
        try:
            body = line[1:].rsplit("*", 1)[0]
            value_hex = body.split(",")[3]
            size = int(value_hex, 16)
            log.info("Log write pointer: 0x%X (%d bytes).", size, size)
            return size
        except (IndexError, ValueError) as exc:
            log.error("Could not parse log write pointer from '%s': %s", line, exc)
            return None
    log.error("No response to $PMTK182,2,7 query.")
    return None


def _flash_size_from_id(flash_id: int) -> int:
    """Decode a flash chip ID (from $PMTK182,2,9) to flash size in bytes.

    Uses the same manufacturer table as BT747Constants.java.
    Supported manufacturers: Macronix (0xC2), EON (0x1C), STMicroelectronics (0x20).
    Default when unknown: 8 MiB.
    """
    KNOWN_MANUFACTURERS = (0xC2, 0x1C, 0x20)  # MX, EON, STM
    manufacturer = (flash_id >> 24) & 0xFF
    dev_type = (flash_id >> 16) & 0xFF
    if manufacturer in KNOWN_MANUFACTURERS and dev_type in (0x20, 0x24, 0x31, 0x30):
        size = 1 << ((flash_id >> 8) & 0xFF)
    else:
        size = 8 * 1024 * 1024  # default: 8 MiB
    log.info("Flash chip ID 0x%08X → %d MiB.", flash_id, size // (1024 * 1024))
    return size


def _query_flash_size(conn: SerialConnection) -> int:
    """Query the flash chip ID via $PMTK182,2,9 and return flash size in bytes.

    Returns the decoded flash size, or a default of 8 MiB on failure.
    """
    log.debug("Querying flash chip ID ($PMTK182,2,9) …")
    conn.send_command("PMTK182,2,9")
    deadline = time.monotonic() + QUERY_TIMEOUT
    while time.monotonic() < deadline:
        line = conn.read_line()
        if not line:
            continue
        log.debug("rx: %s", line)
        if not line.startswith("$PMTK182,3,9"):
            continue
        try:
            body = line[1:].rsplit("*", 1)[0]
            flash_id = int(body.split(",")[3], 16)
            return _flash_size_from_id(flash_id)
        except (IndexError, ValueError) as exc:
            log.error("Could not parse flash ID from '%s': %s", line, exc)
            break
    log.warning("No response to $PMTK182,2,9 – using 8 MiB default.")
    return 8 * 1024 * 1024


def _request_chunk(conn: SerialConnection, addr: int, size: int, timeout: float) -> bytes | None:
    """Send one $PMTK182,7 request and return the data from the $PMTK182,8 reply.

    Args:
        conn:    Open serial connection.
        addr:    Start address (byte offset in flash).
        size:    Number of bytes to request.
        timeout: Max seconds to wait for the reply.

    Returns:
        Raw bytes from the device, or None on timeout/error.
    """
    log.debug("Requesting chunk addr=0x%08X size=0x%X …", addr, size)
    conn.send_command(f"PMTK182,7,{addr:08X},{size:08X}")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = conn.read_line()
        if not line:
            continue
        log.debug("rx: %s", line[:100])

        # Check for ACK of the download sub-command (sub_cmd=7).
        # Format: $PMTK001,182,<sub_cmd>,<result>
        # Ignore ACKs for other sub-commands (e.g. $PMTK001,182,2,3 is the
        # delayed ACK for the previous $PMTK182,2,7 size query – not an error).
        if line.startswith("$PMTK001,182"):
            parts = line[1:].rsplit("*", 1)[0].split(",")
            if len(parts) >= 4 and parts[2] == "7":
                result_code = parts[3]
                if result_code in ("0", "1", "2"):
                    log.error("Device rejected $PMTK182,7 (result=%s): %s", result_code, line)
                    return None
                log.debug("PMTK182,7 ACK (success)")
            else:
                log.debug("Ignoring unrelated ACK: %s", line)
            continue

        if not line.startswith("$PMTK182,8"):
            continue  # ignore NMEA / other lines

        if not _verify_sentence(line):
            log.warning("Checksum mismatch on $PMTK182,8, skipping: %s", line[:80])
            continue

        # Format: $PMTK182,8,<addr_hex>,<hexdata>*XX
        body = line[1:].rsplit("*", 1)[0]
        parts = body.split(",", 3)  # max 4 parts; hex data may contain no commas
        if len(parts) < 4:
            log.warning("Unexpected $PMTK182,8 format: %s", line[:80])
            continue

        try:
            reply_addr = int(parts[2], 16)
        except ValueError:
            log.warning("Cannot parse address in $PMTK182,8: %s", parts[2])
            continue

        if reply_addr != addr:
            log.warning("Got data for addr 0x%X, expected 0x%X – ignoring.", reply_addr, addr)
            continue

        try:
            data = binascii.unhexlify(parts[3])
        except (binascii.Error, ValueError) as exc:
            log.error("Hex decode error in $PMTK182,8: %s", exc)
            return None

        log.debug("Received %d bytes at addr 0x%08X.", len(data), addr)
        return data

    log.warning("Timeout waiting for $PMTK182,8 at addr 0x%08X.", addr)
    return None


def download_log(conn: SerialConnection, progress_callback=None) -> bytes:
    """Download the full flash log from the device using PMTK182.

    Downloads in CHUNK_SIZE blocks; never crosses 0x10000-byte sector boundaries
    (firmware limitation documented in BT747 MTKLogDownloadHandler.java).

    Args:
        conn: Open SerialConnection.
        progress_callback: Optional callable(bytes_received: int).

    Returns:
        Raw binary log data.
    """
    _wakeup(conn)

    # Query the flash chip size first (full flash must be downloaded when the
    # circular buffer has wrapped around).
    flash_size = _query_flash_size(conn)

    write_ptr = _query_log_size(conn)
    if write_ptr is None:
        log.error("Could not determine log write pointer – aborting.")
        return b""

    # If the write pointer is larger than one sector's worth of space before the
    # end, the log has NOT wrapped and we can limit the download.  Otherwise
    # (write_ptr is small, meaning the flash wrapped) we download the full flash.
    # Round up to the nearest 0x10000 sector boundary in both cases.
    if write_ptr > flash_size // 2:
        # Definitely not wrapped – only download up to write pointer
        end_addr = (write_ptr + 0xFFFF) & ~0xFFFF
    else:
        # Wrapped (or nearly full) – download full flash so no data is missed
        end_addr = flash_size
    log.info(
        "Write pointer: 0x%X, flash: 0x%X → downloading 0x%X bytes.",
        write_ptr, flash_size, end_addr,
    )

    buf = bytearray()
    addr = 0
    chunk_timeout = max(QUERY_TIMEOUT, CHUNK_SIZE / 115200 * 10 * 2 + 2)

    while addr < end_addr:
        remaining = end_addr - addr
        chunk = min(CHUNK_SIZE, remaining)

        # Never cross a 0x10000-byte boundary within one request
        until_boundary = ((addr + 0x10000) & ~0xFFFF) - addr
        if chunk > until_boundary:
            chunk = until_boundary

        data = _request_chunk(conn, addr, chunk, timeout=chunk_timeout)
        if data is None:
            log.error("Download failed at addr 0x%08X – aborting.", addr)
            break

        buf.extend(data)
        addr += len(data)

        if progress_callback:
            progress_callback(len(buf))

        if len(data) < chunk:
            # Device returned less than requested – treat as end of data
            log.debug("Short read at 0x%08X (%d < %d) – stopping.", addr, len(data), chunk)
            break

    log.info("Downloaded %d bytes total.", len(buf))
    return bytes(buf)
