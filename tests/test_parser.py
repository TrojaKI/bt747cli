"""Tests for parser.py: binary MTK log parsing.

Synthetic test data is built to match the actual on-device format
as documented in BT747LogConvert.java / BT747Constants.java.

Key format facts:
  - Sector header: 0x200 bytes; FMT_REG at bytes[2:6] (LE uint32)
  - Data starts at offset 0x200 within each sector
  - Records: fields per FMT_REG, terminated by 0x2A ('*') + 1-byte XOR checksum
  - Special records: 7×0xAA + type + 4-byte value + 4×0xBB (16 bytes)
  - UTC: Unix epoch (seconds since 1970-01-01)
"""

import struct
from datetime import datetime, timezone

import pytest

from bt747cli.parser import (
    HEADER_SIZE,
    SECTOR_SIZE,
    GPSRecord,
    _min_record_size,
    _parse_one_record,
    parse_log,
)

# ---------------------------------------------------------------------------
# Common FMT_REG values
# ---------------------------------------------------------------------------

# Minimal: UTC + LAT + LON  (bits 0, 2, 3)
FMT_UTC_LAT_LON = (1 << 0) | (1 << 2) | (1 << 3)

# Typical QStarz default: UTC+VALID+LAT+LON+HEIGHT+SPEED+HEADING+RCR+MS+DIST
FMT_FULL = (
    (1 << 0)   # UTC
    | (1 << 1) # VALID
    | (1 << 2) # LAT
    | (1 << 3) # LON
    | (1 << 4) # HEIGHT
    | (1 << 5) # SPEED
    | (1 << 6) # HEADING
    | (1 << 17) # RCR
    | (1 << 18) # MILLISECOND
    | (1 << 19) # DISTANCE
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pack_record(fmt_reg: int, **values) -> bytes:
    """Build a binary GPS record (without checksum suffix) from field values."""
    defaults = {
        "utc": 1_700_000_000,  # 2023-11-14 (Unix)
        "valid": 0x0001,
        "lat": 48.0, "lon": 16.0,
        "height": 250.0, "speed": 3.6, "heading": 90.0,
        "dsta": 0, "dage": 0,
        "pdop": 200, "hdop": 150, "vdop": 100,
        "nsat": 0x0605,  # 5 used, 6 in-view
        "rcr": 1, "millisecond": 0, "distance": 0.0,
    }
    fmts = {
        "utc": "<I", "valid": "<H",
        "lat": "<d", "lon": "<d",
        "height": "<f", "speed": "<f", "heading": "<f",
        "dsta": "<H", "dage": "<I",
        "pdop": "<H", "hdop": "<H", "vdop": "<H",
        "nsat": "<H",
        "rcr": "<H", "millisecond": "<H", "distance": "<d",
    }
    bit_fields = [
        (0, "utc"), (1, "valid"), (2, "lat"), (3, "lon"), (4, "height"),
        (5, "speed"), (6, "heading"), (7, "dsta"), (8, "dage"),
        (9, "pdop"), (10, "hdop"), (11, "vdop"), (12, "nsat"),
        (17, "rcr"), (18, "millisecond"), (19, "distance"),
    ]
    buf = b""
    for bit, name in bit_fields:
        if fmt_reg & (1 << bit):
            val = values.get(name, defaults[name])
            buf += struct.pack(fmts[name], val)
    return buf


def _add_checksum(record_bytes: bytes) -> bytes:
    """Append 0x2A and XOR checksum to a record byte string."""
    cs = 0
    for b in record_bytes:
        cs ^= b
    return record_bytes + bytes([0x2A, cs & 0xFF])


def _build_sector(fmt_reg: int, records_bytes: bytes) -> bytes:
    """Wrap record bytes in a valid sector (0x200-byte header + data + 0xFF padding)."""
    # Build 20-byte header info; FMT_REG at bytes[2:6]
    header_info = bytes([0x46, 0x05])  # bytes[0-1]: device-specific
    header_info += struct.pack("<I", fmt_reg)    # bytes[2-5]: FMT_REG
    header_info += struct.pack("<H", 0x0102)     # bytes[6-7]: logMode
    header_info += struct.pack("<I", 50)         # bytes[8-11]: logPeriod
    header_info += struct.pack("<I", 0)          # bytes[12-15]: logDistance
    header_info += struct.pack("<I", 0)          # bytes[16-19]: logSpeed
    header = header_info + bytes(HEADER_SIZE - len(header_info))  # pad to 0x200

    data = header + records_bytes
    # Pad to SECTOR_SIZE with 0xFF
    padding = bytes([0xFF] * (SECTOR_SIZE - len(data)))
    return data + padding


# ---------------------------------------------------------------------------
# Tests: _min_record_size
# ---------------------------------------------------------------------------

class TestMinRecordSize:
    def test_utc_lat_lon(self):
        # UTC(4) + LAT(8) + LON(8) = 20
        assert _min_record_size(FMT_UTC_LAT_LON) == 20

    def test_no_fields(self):
        assert _min_record_size(0) == 0


# ---------------------------------------------------------------------------
# Tests: _parse_one_record
# ---------------------------------------------------------------------------

class TestParseOneRecord:
    def test_minimal_record(self):
        ts = 1_700_000_000
        raw_rec = _pack_record(FMT_UTC_LAT_LON, utc=ts, lat=48.5, lon=16.3)
        raw = _add_checksum(raw_rec)
        result = _parse_one_record(raw, 0, FMT_UTC_LAT_LON)
        assert result is not None
        rec, new_offset = result
        assert rec.lat == pytest.approx(48.5)
        assert rec.lon == pytest.approx(16.3)
        assert rec.utc == datetime.fromtimestamp(ts, tz=timezone.utc)
        assert new_offset == len(raw)

    def test_all_ff_returns_none(self):
        data = bytes([0xFF] * 20)
        assert _parse_one_record(data, 0, FMT_UTC_LAT_LON) is None

    def test_bad_checksum_returns_none(self):
        raw_rec = _pack_record(FMT_UTC_LAT_LON, utc=1_000_000, lat=1.0, lon=2.0)
        # Append wrong checksum
        raw = raw_rec + bytes([0x2A, 0x00])
        assert _parse_one_record(raw, 0, FMT_UTC_LAT_LON) is None

    def test_hdop_scaling(self):
        raw_rec = _pack_record(
            (1 << 0) | (1 << 2) | (1 << 3) | (1 << 10),
            utc=1_000_000, lat=48.0, lon=16.0, hdop=150,
        )
        raw = _add_checksum(raw_rec)
        result = _parse_one_record(raw, 0, (1 << 0) | (1 << 2) | (1 << 3) | (1 << 10))
        assert result is not None
        rec, _ = result
        assert rec.hdop == pytest.approx(1.5)

    def test_millisecond_applied_to_utc(self):
        ts = 1_700_000_000
        fmt = (1 << 0) | (1 << 2) | (1 << 3) | (1 << 18)
        raw_rec = _pack_record(fmt, utc=ts, lat=0.0, lon=0.0, millisecond=750)
        raw = _add_checksum(raw_rec)
        result = _parse_one_record(raw, 0, fmt)
        assert result is not None
        rec, _ = result
        assert rec.utc.microsecond == 750_000

    def test_utc_is_unix_epoch(self):
        # 2024-01-01 00:00:00 UTC = 1704067200
        ts = 1_704_067_200
        raw_rec = _pack_record(FMT_UTC_LAT_LON, utc=ts, lat=0.0, lon=0.0)
        raw = _add_checksum(raw_rec)
        result = _parse_one_record(raw, 0, FMT_UTC_LAT_LON)
        assert result is not None
        rec, _ = result
        assert rec.utc == datetime(2024, 1, 1, tzinfo=timezone.utc)

    def test_invalid_utc_top_bit(self):
        # UTC with bit 31 set → invalid
        raw_rec = _pack_record(FMT_UTC_LAT_LON, utc=0x80000001, lat=0.0, lon=0.0)
        raw = _add_checksum(raw_rec)
        assert _parse_one_record(raw, 0, FMT_UTC_LAT_LON) is None


# ---------------------------------------------------------------------------
# Tests: parse_log (integration)
# ---------------------------------------------------------------------------

class TestParseLog:
    def _make_log(self, fmt_reg, record_values: list[dict]) -> bytes:
        records_bytes = b"".join(
            _add_checksum(_pack_record(fmt_reg, **kv)) for kv in record_values
        )
        return _build_sector(fmt_reg, records_bytes)

    def test_empty_log(self):
        raw = bytes([0xFF] * SECTOR_SIZE)
        assert parse_log(raw) == []

    def test_single_record(self):
        raw = self._make_log(
            FMT_UTC_LAT_LON,
            [{"utc": 1_700_000_000, "lat": 48.2, "lon": 16.4}],
        )
        records = parse_log(raw)
        assert len(records) == 1
        assert records[0].lat == pytest.approx(48.2)

    def test_multiple_records(self):
        values = [
            {"utc": 1_700_000_000, "lat": 48.0, "lon": 16.0},
            {"utc": 1_700_000_060, "lat": 48.1, "lon": 16.1},
            {"utc": 1_700_000_120, "lat": 48.2, "lon": 16.2},
        ]
        raw = self._make_log(FMT_UTC_LAT_LON, values)
        records = parse_log(raw)
        assert len(records) == 3
        assert [r.lat for r in records] == pytest.approx([48.0, 48.1, 48.2])

    def test_records_across_two_sectors(self):
        sector0 = self._make_log(
            FMT_UTC_LAT_LON,
            [{"utc": 1_700_000_000, "lat": 1.0, "lon": 2.0}],
        )
        rec_bytes = _add_checksum(_pack_record(FMT_UTC_LAT_LON, utc=1_700_000_060, lat=3.0, lon=4.0))
        sector1 = _build_sector(FMT_UTC_LAT_LON, rec_bytes)
        records = parse_log(sector0 + sector1)
        assert len(records) == 2

    def test_sector_without_valid_fmtreg_skipped(self):
        # Sector with FMT_REG = 0xFFFFFFFF should be skipped
        raw = bytes([0xFF] * SECTOR_SIZE)
        assert parse_log(raw) == []

    def test_fmt_reg_read_from_header_bytes_2to5(self):
        """FMT_REG must be read from bytes[2:6], not bytes[0:4]."""
        raw = self._make_log(
            FMT_UTC_LAT_LON,
            [{"utc": 1_700_000_000, "lat": 51.0, "lon": 0.0}],
        )
        records = parse_log(raw)
        assert len(records) == 1
        assert records[0].lat == pytest.approx(51.0)
