"""Tests for cli.py helpers: date parsing for --from / --to."""

from datetime import datetime, timezone

import click
import pytest

from bt747cli.cli import _parse_date, _parse_date_end


class TestParseDateStart:
    def test_date_only_is_midnight_utc(self):
        dt = _parse_date(None, None, "2026-07-09")
        assert dt == datetime(2026, 7, 9, 0, 0, 0, tzinfo=timezone.utc)

    def test_full_datetime(self):
        dt = _parse_date(None, None, "2026-07-09T12:34:56")
        assert dt == datetime(2026, 7, 9, 12, 34, 56, tzinfo=timezone.utc)

    def test_none_passthrough(self):
        assert _parse_date(None, None, None) is None

    def test_invalid_raises(self):
        with pytest.raises(click.BadParameter):
            _parse_date(None, None, "09.07.2026")


class TestParseDateEnd:
    def test_date_only_is_end_of_day(self):
        # A bare end date must include the whole day (inclusive filter).
        dt = _parse_date_end(None, None, "2026-07-09")
        assert dt == datetime(2026, 7, 9, 23, 59, 59, 999999, tzinfo=timezone.utc)

    def test_full_datetime_unchanged(self):
        dt = _parse_date_end(None, None, "2026-07-09T12:00:00")
        assert dt == datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)

    def test_none_passthrough(self):
        assert _parse_date_end(None, None, None) is None

    def test_invalid_raises(self):
        with pytest.raises(click.BadParameter):
            _parse_date_end(None, None, "notadate")
