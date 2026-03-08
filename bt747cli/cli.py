"""Command-line interface for bt747cli.

Commands
--------
  download  – Download raw binary log from device to a .bin file.
  export    – Parse a .bin file and write GPX (with optional time filter).
  run       – Download + export in one step (no intermediate file kept by default).

Examples
--------
  bt747cli download --port /dev/ttyUSB0 --output raw.bin
  bt747cli export --input raw.bin --output track.gpx
  bt747cli export --input raw.bin --output track.gpx --from 2024-01-01 --to 2024-01-31
  bt747cli export --input raw.bin --output track.gpx --split-days
  bt747cli run --port /dev/ttyUSB0 --output track.gpx --from 2024-06-01
  bt747cli run --port /dev/ttyUSB0 --output tracks/ --split-days
"""

from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import click

from .connection import DEFAULT_BAUD, DEFAULT_PORT, SerialConnection
from .filter import filter_by_time
from .gpx import records_to_gpx
from .parser import GPSRecord, parse_log
from .protocol import download_log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(ctx, param, value: str | None) -> datetime | None:
    """Click callback: parse YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS into a UTC datetime."""
    if value is None:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise click.BadParameter(f"Expected YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS, got '{value}'")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _group_by_day(records: list[GPSRecord]) -> dict[str, list[GPSRecord]]:
    """Group records by UTC date (YYYY-MM-DD).

    Returns an ordered dict mapping date string → record list.
    """
    groups: dict[str, list[GPSRecord]] = defaultdict(list)
    for rec in records:
        if rec.utc is not None:
            day = rec.utc.strftime("%Y-%m-%d")
            groups[day].append(rec)
    return dict(sorted(groups.items()))


def _gpx_path_for_day(output: str, day: str) -> Path:
    """Derive the GPX output path for a given day.

    If *output* ends with `.gpx`, insert the date before the extension:
      track.gpx → track_2024-06-01.gpx
    Otherwise treat *output* as a directory and write <day>.gpx into it.
    """
    p = Path(output)
    if p.suffix.lower() == ".gpx":
        return p.parent / f"{p.stem}_{day}.gpx"
    # Treat as directory
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{day}.gpx"


def _do_export(
    records: list[GPSRecord],
    output: str,
    date_from: datetime | None,
    date_to: datetime | None,
    track_name: str,
    split_days: bool,
) -> None:
    """Apply filter, split if requested, and write GPX file(s)."""
    if date_from or date_to:
        records = filter_by_time(records, start=date_from, end=date_to)
        click.echo(f"  After time filter: {len(records):,} records.")

    if not records:
        click.echo("WARNING: No records to export.", err=True)

    if split_days:
        groups = _group_by_day(records)
        if not groups:
            click.echo("  No records to write.")
            return
        for day, day_records in groups.items():
            gpx_path = _gpx_path_for_day(output, day)
            name = track_name if track_name != "bt747cli track" else day
            gpx_str = records_to_gpx(day_records, track_name=name)
            gpx_path.write_text(gpx_str, encoding="utf-8")
            click.echo(f"  {day}: {len(day_records):>5,} records → {gpx_path}")
    else:
        gpx_str = records_to_gpx(records, track_name=track_name)
        Path(output).write_text(gpx_str, encoding="utf-8")
        click.echo(f"Wrote GPX → {output}")


def _progress_echo(n: int) -> None:
    click.echo(f"\rReceived {n:,} bytes …", nl=False, err=True)


# ---------------------------------------------------------------------------
# CLI root
# ---------------------------------------------------------------------------

@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """bt747cli – Python CLI for MTK-based GPS data loggers."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    _setup_logging(verbose)


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------

@main.command("download")
@click.option("--port", "-p", default=DEFAULT_PORT, show_default=True, help="Serial port device.")
@click.option("--baud", "-b", default=DEFAULT_BAUD, show_default=True, help="Baud rate.")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False), help="Output .bin file.")
@click.option("--timeout", default=300.0, show_default=True, help="Download timeout in seconds.")
@click.pass_context
def cmd_download(ctx, port: str, baud: int, output: str, timeout: float) -> None:
    """Download raw flash-log from the GPS device."""
    click.echo(f"Connecting to {port} at {baud} baud …")
    with SerialConnection(port=port, baud=baud, timeout=timeout) as conn:
        raw = download_log(conn, progress_callback=_progress_echo)

    if not raw:
        click.echo("ERROR: No data received from device.", err=True)
        sys.exit(1)

    out_path = Path(output)
    out_path.write_bytes(raw)
    click.echo(f"\nSaved {len(raw):,} bytes → {out_path}")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@main.command("export")
@click.option("--input", "-i", "input_file", required=True, type=click.Path(exists=True, dir_okay=False), help="Input .bin file.")
@click.option("--output", "-o", required=True, type=click.Path(), help="Output .gpx file, or directory when --split-days is used.")
@click.option("--from", "date_from", default=None, callback=_parse_date, expose_value=True, is_eager=True, help="Start date/time (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS, UTC).")
@click.option("--to", "date_to", default=None, callback=_parse_date, expose_value=True, is_eager=True, help="End date/time (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS, UTC).")
@click.option("--split-days", is_flag=True, default=False, help="Write one GPX file per day.")
@click.option("--track-name", default="bt747cli track", show_default=True, help="Track name in GPX (ignored when --split-days uses date as name).")
@click.pass_context
def cmd_export(ctx, input_file: str, output: str, date_from, date_to, split_days: bool, track_name: str) -> None:
    """Parse a binary log file and export to GPX.

    With --split-days, one GPX file is written per UTC day.
    The output path determines naming:

    \b
      --output track.gpx      → track_2024-06-01.gpx, track_2024-06-02.gpx, …
      --output /tmp/tracks/   → /tmp/tracks/2024-06-01.gpx, …
    """
    raw = Path(input_file).read_bytes()
    click.echo(f"Parsing {len(raw):,} bytes from {input_file} …")

    records = parse_log(raw)
    click.echo(f"  Found {len(records):,} GPS records.")

    _do_export(records, output, date_from, date_to, track_name, split_days)


# ---------------------------------------------------------------------------
# run (download + export combined)
# ---------------------------------------------------------------------------

@main.command("run")
@click.option("--port", "-p", default=DEFAULT_PORT, show_default=True, help="Serial port device.")
@click.option("--baud", "-b", default=DEFAULT_BAUD, show_default=True, help="Baud rate.")
@click.option("--output", "-o", required=True, type=click.Path(), help="Output .gpx file, or directory when --split-days is used.")
@click.option("--save-bin", default=None, type=click.Path(dir_okay=False), help="Also save raw binary to this file.")
@click.option("--from", "date_from", default=None, callback=_parse_date, expose_value=True, is_eager=True, help="Start date/time filter (UTC).")
@click.option("--to", "date_to", default=None, callback=_parse_date, expose_value=True, is_eager=True, help="End date/time filter (UTC).")
@click.option("--split-days", is_flag=True, default=False, help="Write one GPX file per day.")
@click.option("--timeout", default=300.0, show_default=True, help="Download timeout in seconds.")
@click.option("--track-name", default="bt747cli track", show_default=True, help="Track name in GPX.")
@click.pass_context
def cmd_run(ctx, port: str, baud: int, output: str, save_bin, date_from, date_to, split_days: bool, timeout: float, track_name: str) -> None:
    """Download log from device and export directly to GPX."""
    click.echo(f"Connecting to {port} at {baud} baud …")
    with SerialConnection(port=port, baud=baud, timeout=timeout) as conn:
        raw = download_log(conn, progress_callback=_progress_echo)

    click.echo()  # newline after progress

    if not raw:
        click.echo("ERROR: No data received from device.", err=True)
        sys.exit(1)

    if save_bin:
        Path(save_bin).write_bytes(raw)
        click.echo(f"Saved raw binary → {save_bin}")

    click.echo(f"Parsing {len(raw):,} bytes …")
    records = parse_log(raw)
    click.echo(f"  Found {len(records):,} GPS records.")

    _do_export(records, output, date_from, date_to, track_name, split_days)
