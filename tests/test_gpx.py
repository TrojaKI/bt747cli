"""Tests for gpx.py: GPX export."""

from datetime import datetime, timezone
import xml.etree.ElementTree as ET

import pytest

from bt747cli.gpx import records_to_gpx, _to_gpx_builtin
from bt747cli.parser import GPSRecord


def _rec(lat, lon, utc=None, height=None, speed=None, hdop=None):
    rec = GPSRecord()
    rec.lat = lat
    rec.lon = lon
    rec.utc = utc or datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    rec.height = height
    rec.speed = speed
    rec.hdop = hdop
    return rec


@pytest.fixture
def two_records():
    return [
        _rec(48.1, 11.5, datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc), height=520.0, speed=3.6),
        _rec(48.2, 11.6, datetime(2024, 6, 1, 10, 5, 0, tzinfo=timezone.utc), height=530.0, speed=7.2),
    ]


class TestBuiltinGpx:
    """Test the fallback built-in XML writer."""

    def test_valid_xml(self, two_records):
        gpx_str = _to_gpx_builtin(two_records, "test track")
        root = ET.fromstring(gpx_str)  # raises on invalid XML
        assert root is not None

    def test_trackpoints_count(self, two_records):
        gpx_str = _to_gpx_builtin(two_records, "test track")
        root = ET.fromstring(gpx_str)
        ns = "http://www.topografix.com/GPX/1/1"
        trkpts = root.findall(f".//{{{ns}}}trkpt")
        assert len(trkpts) == 2

    def test_coordinates(self, two_records):
        gpx_str = _to_gpx_builtin(two_records, "test")
        root = ET.fromstring(gpx_str)
        ns = "http://www.topografix.com/GPX/1/1"
        pts = root.findall(f".//{{{ns}}}trkpt")
        assert float(pts[0].attrib["lat"]) == pytest.approx(48.1)
        assert float(pts[0].attrib["lon"]) == pytest.approx(11.5)

    def test_elevation(self, two_records):
        gpx_str = _to_gpx_builtin(two_records, "test")
        root = ET.fromstring(gpx_str)
        ns = "http://www.topografix.com/GPX/1/1"
        ele = root.find(f".//{{{ns}}}trkpt/{{{ns}}}ele")
        assert ele is not None
        assert float(ele.text) == pytest.approx(520.0)

    def test_time_format(self, two_records):
        gpx_str = _to_gpx_builtin(two_records, "test")
        assert "2024-06-01T10:00:00" in gpx_str

    def test_track_name(self, two_records):
        gpx_str = _to_gpx_builtin(two_records, "my special track")
        assert "my special track" in gpx_str

    def test_empty_records(self):
        gpx_str = _to_gpx_builtin([], "empty")
        root = ET.fromstring(gpx_str)
        ns = "http://www.topografix.com/GPX/1/1"
        trkpts = root.findall(f".//{{{ns}}}trkpt")
        assert trkpts == []

    def test_records_without_coords_skipped(self):
        rec = GPSRecord()
        rec.lat = None
        rec.lon = None
        gpx_str = _to_gpx_builtin([rec], "test")
        root = ET.fromstring(gpx_str)
        ns = "http://www.topografix.com/GPX/1/1"
        assert root.findall(f".//{{{ns}}}trkpt") == []


class TestRecordsToGpx:
    """Test the public API which may use gpxpy or fallback."""

    def test_produces_string(self, two_records):
        result = records_to_gpx(two_records)
        assert isinstance(result, str)

    def test_contains_gpx_root(self, two_records):
        result = records_to_gpx(two_records)
        assert "gpx" in result.lower()
