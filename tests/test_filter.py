"""Tests for filter.py: time-based GPS record filtering."""

from datetime import datetime, timezone

import pytest

from bt747cli.filter import filter_by_time
from bt747cli.parser import GPSRecord


def _rec(year, month, day, hour=0, minute=0, second=0):
    """Helper to create a GPSRecord with a UTC timestamp."""
    rec = GPSRecord()
    rec.utc = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    rec.lat = 0.0
    rec.lon = 0.0
    return rec


@pytest.fixture
def sample_records():
    return [
        _rec(2024, 1, 1, 10, 0, 0),
        _rec(2024, 1, 15, 12, 0, 0),
        _rec(2024, 2, 1, 8, 0, 0),
        _rec(2024, 3, 1, 9, 0, 0),
    ]


class TestFilterByTime:
    def test_no_filter(self, sample_records):
        result = filter_by_time(sample_records)
        assert len(result) == 4

    def test_start_only(self, sample_records):
        start = datetime(2024, 2, 1, tzinfo=timezone.utc)
        result = filter_by_time(sample_records, start=start)
        assert len(result) == 2  # Feb 1 and Mar 1

    def test_end_only(self, sample_records):
        end = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = filter_by_time(sample_records, end=end)
        assert len(result) == 2  # Jan 1 and Jan 15

    def test_start_and_end(self, sample_records):
        start = datetime(2024, 1, 15, tzinfo=timezone.utc)
        end = datetime(2024, 2, 1, 23, 59, 59, tzinfo=timezone.utc)
        result = filter_by_time(sample_records, start=start, end=end)
        assert len(result) == 2  # Jan 15 and Feb 1

    def test_empty_result(self, sample_records):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = filter_by_time(sample_records, start=start)
        assert result == []

    def test_naive_datetimes_treated_as_utc(self, sample_records):
        # Naive datetime should be treated as UTC
        start = datetime(2024, 2, 1)  # naive
        result = filter_by_time(sample_records, start=start)
        assert len(result) == 2

    def test_records_without_utc_skipped(self):
        rec_no_time = GPSRecord()
        rec_no_time.lat = 0.0
        rec_no_time.lon = 0.0
        rec_no_time.utc = None
        rec_with_time = _rec(2024, 6, 1)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = filter_by_time([rec_no_time, rec_with_time], start=start)
        assert len(result) == 1
        assert result[0] is rec_with_time

    def test_inclusive_boundaries(self, sample_records):
        start = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        result = filter_by_time(sample_records, start=start, end=end)
        assert len(result) == 1
