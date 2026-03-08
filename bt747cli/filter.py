"""Time-based filter for GPS records."""

from __future__ import annotations

from datetime import datetime, timezone

from .parser import GPSRecord


def _to_utc(dt: datetime) -> datetime:
    """Ensure *dt* is timezone-aware (UTC).  Naive datetimes are assumed UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def filter_by_time(
    records: list[GPSRecord],
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[GPSRecord]:
    """Return only records whose UTC timestamp falls within [start, end].

    Args:
        records: Input GPS records (must have a valid utc field).
        start:   Inclusive start datetime (None = no lower bound).
        end:     Inclusive end datetime (None = no upper bound).

    Returns:
        Filtered list of GPSRecord objects.
    """
    utc_start = _to_utc(start) if start else None
    utc_end = _to_utc(end) if end else None

    result: list[GPSRecord] = []
    for rec in records:
        if rec.utc is None:
            continue
        rec_utc = _to_utc(rec.utc)
        if utc_start and rec_utc < utc_start:
            continue
        if utc_end and rec_utc > utc_end:
            continue
        result.append(rec)
    return result
