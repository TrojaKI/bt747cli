# bt747cli

A lightweight Python CLI for MTK-based GPS data loggers (QStarz BT-Q1000, i-Blue 747, Holux M-241, and compatible devices).

Replaces the heavyweight Java BT747 application with a focused command-line tool for Linux that:
- Downloads the raw flash log from the device
- Exports GPS tracks as GPX (single file or one file per day)
- Filters exported tracks by time range

---

## Supported Devices

All devices based on the MediaTek (MTK) chipset, including:

- QStarz BT-Q1000 / BT-Q1000X
- i-Blue 747 / 757
- i.Trek Z1
- Holux GR-241 / M-241

---

## Requirements

- Python 3.10+
- USB cable and kernel module `cp210x` or `ftdi_sio` (usually loaded automatically)
- Device accessible at `/dev/ttyACM0` (or configure via `--port`)

Python dependencies (installed automatically):
- `pyserial` – serial communication
- `click` – CLI framework
- `gpxpy` – GPX generation

---

## Installation

```bash
git clone <repo>
cd bt747cli

python3 -m venv venv
source venv/bin/activate
pip install -e .
```

To verify the installation:

```bash
bt747cli --help
```

### udev rule (optional)

If you get a permission error on `/dev/ttyACM0`, add a udev rule so your user can access the port without `sudo`:

```bash
echo 'SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", MODE="0666"' \
  | sudo tee /etc/udev/rules.d/99-gps-logger.rules
sudo udevadm control --reload-rules
```

Reconnect the device afterwards.

---

## Usage

### Download raw log from device

Downloads the **full flash** and saves it as a binary file.  The tool automatically
detects the flash chip size via `$PMTK182,2,9` and downloads everything, so data
is not lost when the circular buffer has wrapped around.

```bash
bt747cli download --output raw.bin
```

Options:

| Option | Default | Description |
|--------|---------|-------------|
| `--port`, `-p` | `/dev/ttyACM0` | Serial port |
| `--baud`, `-b` | `115200` | Baud rate |
| `--output`, `-o` | *(required)* | Output `.bin` file |
| `--timeout` | `300.0` | Download timeout in seconds |

---

### Export binary log to GPX

Parses a previously downloaded `.bin` file and writes a GPX track.

```bash
bt747cli export --input raw.bin --output track.gpx
```

With a time filter:

```bash
bt747cli export --input raw.bin --output track.gpx \
  --from 2025-06-01 --to 2025-06-30
```

One GPX file per UTC day (useful for long logs spanning many days):

```bash
# All days into a directory
bt747cli export --input raw.bin --output tracks/ --split-days

# All days, named track_YYYY-MM-DD.gpx
bt747cli export --input raw.bin --output track.gpx --split-days

# Only a specific date range, split by day
bt747cli export --input raw.bin --output tracks/ --split-days \
  --from 2026-01-07 --to 2026-03-07
```

Options:

| Option | Default | Description |
|--------|---------|-------------|
| `--input`, `-i` | *(required)* | Input `.bin` file |
| `--output`, `-o` | *(required)* | Output `.gpx` file or directory (with `--split-days`) |
| `--from` | *(none)* | Start datetime, inclusive (UTC) |
| `--to` | *(none)* | End datetime, inclusive (UTC) |
| `--split-days` | off | Write one GPX file per UTC day |
| `--track-name` | `bt747cli track` | Track name in GPX (ignored for `--split-days`, which uses the date) |

Date format: `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SS` (always interpreted as UTC).

**`--split-days` output naming:**

| `--output` value | Resulting files |
|-----------------|-----------------|
| `track.gpx` | `track_2026-03-07.gpx`, `track_2026-03-08.gpx`, … |
| `tracks/` | `tracks/2026-03-07.gpx`, `tracks/2026-03-08.gpx`, … |

---

### Download and export in one step

```bash
bt747cli run --output track.gpx
```

With time filter and saving the raw binary as well:

```bash
bt747cli run --output track.gpx \
  --from 2025-06-01T08:00:00 --to 2025-06-01T18:00:00 \
  --save-bin raw.bin
```

Split by day directly after download:

```bash
bt747cli run --output tracks/ --split-days
```

Options: all options from `download` and `export` combined, plus `--save-bin`.

---

### Verbose / debug output

Add `-v` / `--verbose` before the subcommand to enable debug logging:

```bash
bt747cli -v download --output raw.bin
```

---

## Examples

```bash
# Full workflow: download once, export as needed
bt747cli download --port /dev/ttyACM0 --output raw.bin
bt747cli export --input raw.bin --output tracks/ --split-days

# Only today's track
bt747cli export --input raw.bin --output today.gpx --from 2026-03-07

# Single time window
bt747cli export --input raw.bin --output morning.gpx \
  --from 2026-03-07T06:00:00 --to 2026-03-07T12:00:00

# Everything in one command, split by day
bt747cli run --port /dev/ttyACM0 --output tracks/ --split-days --save-bin raw.bin
```

The resulting `.gpx` files can be opened in QGIS, gpx.studio, OsmAnd, or any other GPX-capable application.

---

## Running Tests

```bash
source venv/bin/activate
pip install pytest gpxpy
pytest tests/ -v
```

All tests run without a physical device connected – the parser and filter tests use synthetic binary data.

---

## Architecture

```
bt747cli/
├── connection.py   # Serial port, PMTK checksum, send/receive
├── protocol.py     # PMTK182 download protocol, flash size detection
├── parser.py       # Binary flash-log parser → GPSRecord dataclasses
├── filter.py       # Time-range filter
├── gpx.py          # GPX 1.1 export (gpxpy or built-in XML fallback)
└── cli.py          # Click-based CLI entry point
```

### Protocol notes

- Communication: NMEA-style `$PMTK…` commands with XOR checksum, 115200 baud
- Download command set: `$PMTK182` (not `$PMTK622`, which is unsupported on tested devices)
  - `$PMTK182,2,7` → query current write pointer (circular buffer position)
  - `$PMTK182,2,9` → query flash chip ID → decode total flash size
  - `$PMTK182,7,<addr>,<len>` → request data chunk; reply: `$PMTK182,8,<addr>,<hexdata>`
- Flash is a **circular buffer**: when full it wraps and overwrites the oldest data.
  The write pointer from `$PMTK182,2,7` will be small after a wrap — the full flash
  must be downloaded to recover all data.
- Log format: 64 KiB sectors with 512-byte headers; GPS records with variable-length
  fields controlled by a 32-bit `FMT_REG` bitmask read from each sector header
- UTC timestamps: Unix epoch (seconds since 1970-01-01), not GPS epoch

---

## Known Limitations

- **No erase / clear log**: the tool is read-only; use the original BT747 application or device buttons to erase the log.
- **No Bluetooth support**: USB only. Bluetooth serial (`/dev/rfcomm0`) may work but is untested.
- **Satellite data fields** (SID, elevation, azimuth, SNR) are skipped during parsing – they are present in the raw binary but not exported to GPX.
- Different firmware versions may use different default `FMT_REG` values; the parser reads the register from each sector header and adapts automatically.

---

## License

GPL-2.0 – same as the original BT747 project.
