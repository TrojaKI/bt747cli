"""Binary MTK flash-log parser.

Reverse-engineered from BT747 source:
  src/gps/log/in/BT747LogConvert.java
  src/gps/BT747Constants.java

Flash memory layout
-------------------
The flash is divided into sectors of 0x10000 bytes (65536). Each sector starts
with a 0x200-byte (512-byte) header block.  GPS records follow immediately at
offset 0x200 within each sector.

Sector header (only bytes 0-19 are defined; rest of 0x200 is unused):
  bytes[0-1]  : device-specific (ignored)
  bytes[2-5]  : FMT_REG (little-endian uint32) – field bitmask
  bytes[6-7]  : logMode (little-endian uint16)
  bytes[8-11] : logPeriod (uint32, milliseconds)
  bytes[12-15]: logDistance (uint32, cm)
  bytes[16-19]: logSpeed (uint32, cm/s)

GPS record format (variable length, determined by FMT_REG):
  - Fields in bit-index order (0..19), each field present when its bit is set
  - Terminated by: 0x2A ('*') + 1-byte XOR checksum of all record bytes
  - For non-Holux devices: skip 0x2A + checksum byte (2 bytes total)

Special 16-byte control records (change log parameters mid-stream):
  bytes[0-6]  : 0xAA (seven bytes)
  byte[7]     : type (0x02=fmt change, 0x03=period, 0x04=distance, 0x05=speed)
  bytes[8-11] : value (uint32 little-endian)
  bytes[12-15]: 0xBB (four bytes)

FMT_REG bit indices and field sizes (from BT747Constants.logFmtByteSizes):
  0  UTC        4 bytes  (uint32, Unix epoch, seconds since 1970-01-01)
  1  VALID      2 bytes  (uint16)
  2  LATITUDE   8 bytes  (double, degrees)
  3  LONGITUDE  8 bytes  (double, degrees)
  4  HEIGHT     4 bytes  (float, metres WGS84)
  5  SPEED      4 bytes  (float, km/h)
  6  HEADING    4 bytes  (float, degrees)
  7  DSTA       2 bytes  (uint16)
  8  DAGE       4 bytes  (uint32)
  9  PDOP       2 bytes  (uint16, raw integer)
  10 HDOP       2 bytes  (uint16, raw integer)
  11 VDOP       2 bytes  (uint16, raw integer)
  12 NSAT       2 bytes  (uint16, used/inview packed)
  13 SID        4 bytes  per satellite (uint8 id + uint8 inuse + uint16 satcnt)
  14 ELEVATION  2 bytes  per satellite (int16)
  15 AZIMUTH    2 bytes  per satellite (uint16)
  16 SNR        2 bytes  per satellite (uint16)
  17 RCR        2 bytes  (uint16, reason for recording)
  18 MILLISECOND 2 bytes (uint16)
  19 DISTANCE   8 bytes  (double, metres total)
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

SECTOR_SIZE = 0x10000       # 65536 bytes per flash sector
HEADER_SIZE = 0x200         # 512 bytes: sector header + padding before data

# Field definitions: (bit_index, name, struct_format, byte_size)
# Satellite fields (bits 13-16) have variable count and are handled separately.
_FIXED_FIELDS: list[tuple[int, str, str, int]] = [
    (0,  "utc",         "<I", 4),
    (1,  "valid",       "<H", 2),
    (2,  "lat",         "<d", 8),
    (3,  "lon",         "<d", 8),
    (4,  "height",      "<f", 4),
    (5,  "speed",       "<f", 4),
    (6,  "heading",     "<f", 4),
    (7,  "dsta",        "<H", 2),
    (8,  "dage",        "<I", 4),
    (9,  "pdop",        "<H", 2),
    (10, "hdop",        "<H", 2),
    (11, "vdop",        "<H", 2),
    (12, "nsat",        "<H", 2),
    # bits 13-16: satellite blocks – variable per-sat data, handled below
    (17, "rcr",         "<H", 2),
    (18, "millisecond", "<H", 2),
    (19, "distance",    "<d", 8),
]

# Bytes per satellite for each satellite sub-field
_SAT_BYTES_PER = {13: 4, 14: 2, 15: 2, 16: 2}


@dataclass
class GPSRecord:
    """One GPS fix parsed from the device log."""

    utc: datetime | None = None
    lat: float | None = None
    lon: float | None = None
    height: float | None = None       # metres (WGS84)
    speed: float | None = None        # km/h
    heading: float | None = None      # degrees
    hdop: float | None = None
    vdop: float | None = None
    pdop: float | None = None
    nsat_used: int | None = None
    nsat_inview: int | None = None
    valid: int | None = None
    rcr: int | None = None
    millisecond: int | None = None
    distance: float | None = None

    @property
    def is_valid(self) -> bool:
        return self.lat is not None and self.lon is not None and self.utc is not None


def _min_record_size(fmt_reg: int) -> int:
    """Return the minimum number of data bytes in one record (excluding `*CS`)."""
    size = 0
    for bit, _name, _fmt, nbytes in _FIXED_FIELDS:
        if fmt_reg & (1 << bit):
            size += nbytes
    # Satellite fields: each requires nsat * bytes_per_sat, but nsat is 0
    # at minimum so they don't add to the minimum size here.
    return size


def _is_special_record(data: bytes, offset: int) -> bool:
    """Return True when a 16-byte special record starts at *offset*."""
    if offset + 16 > len(data):
        return False
    return (
        all(data[offset + i] == 0xAA for i in range(7))
        and data[offset + 12] == 0xBB
        and data[offset + 13] == 0xBB
        and data[offset + 14] == 0xBB
        and data[offset + 15] == 0xBB
    )


def _parse_special_record(data: bytes, offset: int) -> tuple[int, int]:
    """Parse a 16-byte special record.

    Returns (type, value).  type 0x02 = FMT_REG change.
    """
    rec_type = data[offset + 7]
    value = struct.unpack_from("<I", data, offset + 8)[0]
    return rec_type, value


def _parse_one_record(
    data: bytes, offset: int, fmt_reg: int
) -> tuple[GPSRecord, int] | None:
    """Try to parse one GPS record starting at *offset*.

    Returns (GPSRecord, next_offset) on success, or None when no valid record
    is found (e.g. end of data, all-0xFF padding, or checksum mismatch).
    """
    if offset + _min_record_size(fmt_reg) + 2 > len(data):
        return None

    # All-0xFF at this position means end of written data.
    if all(data[offset + i] == 0xFF for i in range(min(4, len(data) - offset))):
        return None

    rec = GPSRecord()
    pos = offset
    nsat = 0  # satellite count, needed to skip per-sat fields
    checksum = 0

    for bit, name, fmt, nbytes in _FIXED_FIELDS:
        if not (fmt_reg & (1 << bit)):
            continue
        if pos + nbytes > len(data):
            return None
        raw_bytes = data[pos: pos + nbytes]
        value = struct.unpack(fmt, raw_bytes)[0]
        for b in raw_bytes:
            checksum ^= b
        pos += nbytes

        if name == "utc":
            if value & 0x80000000:
                return None  # invalid timestamp (sign bit set)
            rec.utc = datetime.fromtimestamp(value, tz=timezone.utc)
        elif name == "lat":
            if not -90.0 <= value <= 90.0:
                return None
            rec.lat = value
        elif name == "lon":
            if not -180.0 <= value <= 180.0:
                return None
            rec.lon = value
        elif name == "height":
            if rec.valid is not None and (rec.valid & 0x01) != 1:
                if value < -3000.0 or value > 15000.0:
                    return None
            rec.height = value
        elif name == "speed":
            if value < -10.0:
                return None
            rec.speed = value
        elif name == "heading":
            rec.heading = value
        elif name == "hdop":
            rec.hdop = value / 100.0
        elif name == "vdop":
            rec.vdop = value / 100.0
        elif name == "pdop":
            rec.pdop = value / 100.0
        elif name == "nsat":
            rec.nsat_used = value & 0xFF
            rec.nsat_inview = (value >> 8) & 0xFF
            nsat = rec.nsat_used
        elif name == "valid":
            rec.valid = value
        elif name == "rcr":
            rec.rcr = value
        elif name == "millisecond":
            rec.millisecond = value
        elif name == "distance":
            rec.distance = value

        # Insert satellite per-field data right after NSAT (bit 12)
        if bit == 12:
            for sat_bit, bytes_per_sat in _SAT_BYTES_PER.items():
                if fmt_reg & (1 << sat_bit):
                    sat_bytes = nsat * bytes_per_sat
                    for b in data[pos: pos + sat_bytes]:
                        checksum ^= b
                    pos += sat_bytes

    # Verify the record terminator: 0x2A ('*') followed by XOR checksum byte
    if pos + 2 > len(data):
        return None
    if data[pos] != 0x2A:
        return None
    if (checksum & 0xFF) != data[pos + 1]:
        log.debug(
            "Checksum mismatch at 0x%X: expected 0x%02X got 0x%02X",
            offset, checksum & 0xFF, data[pos + 1],
        )
        return None

    pos += 2  # skip '*' and checksum byte

    # Apply millisecond precision to UTC
    if rec.utc is not None and rec.millisecond:
        from datetime import timedelta
        rec.utc = rec.utc + timedelta(milliseconds=rec.millisecond)

    return rec, pos


def parse_log(raw: bytes) -> list[GPSRecord]:
    """Parse the full binary flash log and return a list of GPSRecord objects.

    Processes all 0x10000-byte sectors. Each sector starts with a 0x200-byte
    header block; the FMT_REG is read from bytes[2:6] of that header.

    Args:
        raw: Raw bytes downloaded from the device (or loaded from a .bin file).

    Returns:
        List of parsed GPS records, in the order they appear in the log.
    """
    records: list[GPSRecord] = []
    fmt_reg: int = 0
    total = len(raw)

    sector_start = 0
    while sector_start < total:
        # --- Read sector header ---
        if sector_start + HEADER_SIZE > total:
            break

        # FMT_REG at bytes[2:6] of the sector header
        candidate = struct.unpack_from("<I", raw, sector_start + 2)[0]
        if candidate not in (0x00000000, 0xFFFFFFFF):
            fmt_reg = candidate
            log.debug("Sector 0x%X: FMT_REG=0x%08X", sector_start, fmt_reg)
        elif fmt_reg == 0:
            log.warning("Sector 0x%X: FMT_REG unknown, skipping.", sector_start)
            sector_start += SECTOR_SIZE
            continue

        if fmt_reg == 0:
            sector_start += SECTOR_SIZE
            continue

        # --- Parse records within this sector ---
        data_start = sector_start + HEADER_SIZE
        data_end = min(sector_start + SECTOR_SIZE, total)
        offset = data_start

        while offset < data_end:
            # Check for special control record (AA×7 … BB×4)
            if _is_special_record(raw, offset):
                rec_type, value = _parse_special_record(raw, offset)
                if rec_type == 0x02:  # FMT_REG change
                    fmt_reg = value
                    log.debug(
                        "FMT_REG changed at 0x%X → 0x%08X", offset, fmt_reg
                    )
                offset += 16
                continue

            # All 0xFF → end of written log data in this sector
            if all(raw[offset + i] == 0xFF for i in range(min(4, data_end - offset))):
                break

            result = _parse_one_record(raw, offset, fmt_reg)
            if result is None:
                # Try to skip one byte and recover (mirrors BT747 recovery logic)
                offset += 1
                continue

            rec, offset = result
            if rec.is_valid:
                records.append(rec)

        sector_start += SECTOR_SIZE

    log.info("Parsed %d valid GPS records.", len(records))
    return records
