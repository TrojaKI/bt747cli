"""GPX export for GPS records.

Uses gpxpy to generate standards-compliant GPX 1.1 output.
Falls back to a minimal hand-crafted XML writer when gpxpy is unavailable.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .parser import GPSRecord

log = logging.getLogger(__name__)


def records_to_gpx(records: list[GPSRecord], track_name: str = "bt747cli track") -> str:
    """Convert GPS records to a GPX 1.1 string.

    Args:
        records:    List of GPSRecord objects to export.
        track_name: Name embedded in the <name> element of the GPX track.

    Returns:
        GPX XML as a string.
    """
    try:
        return _to_gpx_via_gpxpy(records, track_name)
    except ImportError:
        log.warning("gpxpy not available, using built-in XML writer.")
        return _to_gpx_builtin(records, track_name)


def _to_gpx_via_gpxpy(records: list[GPSRecord], track_name: str) -> str:
    import gpxpy
    import gpxpy.gpx

    gpx = gpxpy.gpx.GPX()
    gpx.creator = "bt747cli"

    track = gpxpy.gpx.GPXTrack(name=track_name)
    gpx.tracks.append(track)

    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)

    for rec in records:
        if rec.lat is None or rec.lon is None:
            continue
        point = gpxpy.gpx.GPXTrackPoint(
            latitude=rec.lat,
            longitude=rec.lon,
            elevation=rec.height,
            time=rec.utc,
        )
        if rec.speed is not None:
            point.speed = rec.speed / 3.6  # km/h → m/s  (GPX uses m/s)
        if rec.hdop is not None:
            point.horizontal_dilution = rec.hdop
        if rec.vdop is not None:
            point.vertical_dilution = rec.vdop
        if rec.pdop is not None:
            point.position_dilution = rec.pdop
        if rec.nsat_used is not None:
            point.satellites = rec.nsat_used
        segment.points.append(point)

    return gpx.to_xml()


def _to_gpx_builtin(records: list[GPSRecord], track_name: str) -> str:
    """Minimal GPX 1.1 writer that does not require gpxpy."""
    import xml.etree.ElementTree as ET

    NS = "http://www.topografix.com/GPX/1/1"
    XSI = "http://www.w3.org/2001/XMLSchema-instance"

    root = ET.Element(
        "gpx",
        attrib={
            "version": "1.1",
            "creator": "bt747cli",
            "xmlns": NS,
            "xmlns:xsi": XSI,
            "xsi:schemaLocation": (
                f"{NS} http://www.topografix.com/GPX/1/1/gpx.xsd"
            ),
        },
    )

    trk = ET.SubElement(root, "trk")
    ET.SubElement(trk, "name").text = track_name
    trkseg = ET.SubElement(trk, "trkseg")

    for rec in records:
        if rec.lat is None or rec.lon is None:
            continue
        trkpt = ET.SubElement(
            trkseg,
            "trkpt",
            attrib={"lat": f"{rec.lat:.8f}", "lon": f"{rec.lon:.8f}"},
        )
        if rec.height is not None:
            ET.SubElement(trkpt, "ele").text = f"{rec.height:.2f}"
        if rec.utc is not None:
            ET.SubElement(trkpt, "time").text = rec.utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        if rec.hdop is not None:
            ET.SubElement(trkpt, "hdop").text = f"{rec.hdop:.2f}"
        if rec.vdop is not None:
            ET.SubElement(trkpt, "vdop").text = f"{rec.vdop:.2f}"
        if rec.pdop is not None:
            ET.SubElement(trkpt, "pdop").text = f"{rec.pdop:.2f}"
        if rec.nsat_used is not None:
            ET.SubElement(trkpt, "sat").text = str(rec.nsat_used)

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
