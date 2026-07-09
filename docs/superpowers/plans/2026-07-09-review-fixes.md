# Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four critical findings from the 2026-07-09 code review: ring-buffer data loss on download, `--to` excluding the whole end day, download timeout misused as serial read timeout, and a broken `run.sh` wrapper.

**Architecture:** All changes are surgical fixes inside the existing modules (`cli.py`, `protocol.py`, `connection.py`, `run.sh`). The download-size decision is ported from the BT747 Java reference (`src/bt747/model/Controller.java:855-866`): query the recording method via `$PMTK182,2,6`; in OVERLAP mode (or unknown) download the full flash, in STOP mode only up to the write pointer. A small generic `_query()` helper replaces the three copy-pasted query loops in `protocol.py`.

**Tech Stack:** Python 3.10+, click, pyserial, pytest (all tests run without a device, using `MagicMock` connections). Bash for `run.sh`.

**Repo:** `/home/peter/dev/ki_tools/claude/bt747/bt747cli/` (git, branch `main`, no direct pushes to main).

**Run tests with:** `venv/bin/python -m pytest tests/ -v` (from the repo root; the venv already contains all dependencies — do NOT pip-install anything globally).

**Protocol background (needed for Task 2):**
- Device replies `$PMTK182,3,<param>,<value>*CS` to queries `$PMTK182,2,<param>`.
- Param 6 = recording method: value `1` = OVERLAP (ring buffer, overwrites oldest when full), `2` = STOP (logging stops when full). Verified against `MtkModel.java:492`: `logFullOverwrite = (toInt(sNmea[3]) == 1)`.
- Param 7 = next-write-address (hex), param 9 = flash chip ID (hex). Already implemented.
- In OVERLAP mode the write pointer says nothing about how much valid data exists (it may have wrapped), so the full flash must be downloaded. Project CLAUDE.md documents this: "Flash = Ringpuffer (überschreibt nach Volllauf das Älteste → immer voll laden)".

**Out of scope (deferred findings — do NOT fix these):** SID satellite-count parsing, GPX fallback element order, `/dev/ttyUSB0` vs `/dev/ttyACM0` default, traceback on missing port, dead code removal beyond what these tasks touch.

---

### Task 0: Create working branch

**Files:** none (git only)

- [ ] **Step 1: Verify clean state and create branch**

```bash
cd /home/peter/dev/ki_tools/claude/bt747/bt747cli
git status --short   # expected: empty (plan file may show as untracked — that is fine)
git checkout -b fix/review-findings
```

- [ ] **Step 2: Run the full suite once to confirm a green baseline**

Run: `venv/bin/python -m pytest tests/ -v`
Expected: 53 passed.

Note: if pytest or the CLI dependencies are missing, the venv was rebuilt for a new
system Python — recreate it with `python3 -m venv --clear venv && venv/bin/pip install -e . pytest`.

- [ ] **Step 3: Commit the plan file**

```bash
git add docs/superpowers/plans/2026-07-09-review-fixes.md
git commit -m "docs: add implementation plan for review fixes"
```

---

### Task 1: `--to` with bare date must include the whole end day

A bare `--to 2026-07-09` currently parses to `2026-07-09T00:00:00Z`, so the inclusive filter in `filter.py` drops every record of that day. Fix: expand a date-only `--to` value to `23:59:59.999999` of that day. `--from` keeps midnight semantics.

**Files:**
- Modify: `bt747cli/cli.py:40-49` (replace `_parse_date`, add `_parse_datetime` + `_parse_date_end`), `bt747cli/cli.py:173` and `bt747cli/cli.py:206` (`--to` callbacks)
- Test: `tests/test_cli.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_cli.py -v`
Expected: FAIL / ERROR with `ImportError: cannot import name '_parse_date_end'`.

- [ ] **Step 3: Implement the parsing split in `bt747cli/cli.py`**

Add `timedelta` to the existing datetime import (line 24):

```python
from datetime import datetime, timedelta, timezone
```

Replace the existing `_parse_date` function (lines 40-49) with:

```python
def _parse_datetime(value: str, *, end_of_day: bool) -> datetime:
    """Parse YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS into a UTC datetime.

    Date-only values expand to 00:00:00, or to 23:59:59.999999 when
    *end_of_day* is set — so a bare end date includes the whole day.
    """
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise click.BadParameter(f"Expected YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS, got '{value}'")
    if end_of_day:
        dt += timedelta(days=1) - timedelta(microseconds=1)
    return dt


def _parse_date(ctx, param, value: str | None) -> datetime | None:
    """Click callback for --from."""
    if value is None:
        return None
    return _parse_datetime(value, end_of_day=False)


def _parse_date_end(ctx, param, value: str | None) -> datetime | None:
    """Click callback for --to: a bare date means end of that day (inclusive)."""
    if value is None:
        return None
    return _parse_datetime(value, end_of_day=True)
```

Then switch the two `--to` options to the new callback:

In `cmd_export` (line 173) and `cmd_run` (line 206), change `callback=_parse_date` to `callback=_parse_date_end` **only on the `--to` options** (`"date_to"`). The `--from` options keep `callback=_parse_date`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_cli.py -v`
Expected: 8 passed.

- [ ] **Step 5: Run the full suite (regression check)**

Run: `venv/bin/python -m pytest tests/ -v`
Expected: 61 passed, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add bt747cli/cli.py tests/test_cli.py
git commit -m "fix(cli): make bare --to date include the whole end day"
```

---

### Task 2: Ring-buffer-safe download size via recording-method query

The current heuristic (`write_ptr > flash_size // 2` → not wrapped) silently loses the oldest data when the ring buffer has wrapped, and wastes a full-flash download for small logs. Port the BT747 logic: query `$PMTK182,2,6`; STOP mode → download up to write pointer (sector-rounded), OVERLAP or unknown → full flash. Also DRY up the three copy-pasted query loops into one `_query()` helper.

**Files:**
- Modify: `bt747cli/protocol.py` (add `_query`, `_query_rec_method`, `_compute_end_addr`, constants; rewrite `_query_log_size`, `_query_flash_size`; replace heuristic in `download_log:254-267`)
- Test: `tests/test_protocol.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_protocol.py` (note: `_make_pmtk182_8` and the imports of `MagicMock`/`patch` already exist at the top of the file):

```python
from bt747cli.protocol import (
    REC_METHOD_OVERLAP,
    REC_METHOD_STOP,
    _compute_end_addr,
    _query,
    _query_rec_method,
)


def _sentence(payload: str) -> str:
    """Build a full PMTK sentence with valid checksum."""
    return f"${payload}*{_pmtk_checksum(payload)}"


def _make_conn(lines: list[str]):
    """Mock connection yielding *lines* one by one, then '' forever."""
    conn = MagicMock()
    it = iter(lines)
    conn.read_line.side_effect = lambda: next(it, "")
    return conn


class TestQuery:
    def test_returns_value_field(self):
        conn = _make_conn([_sentence("PMTK182,3,6,2")])
        assert _query(conn, 6) == "2"

    def test_ignores_unrelated_replies(self):
        conn = _make_conn([
            _sentence("PMTK182,3,7,0000AB00"),  # reply to a different param
            "$GPGGA,123456.000,4800.0,N,01100.0,E,1,8,1.0,100.0,M,0.0,M,,*XX",
            _sentence("PMTK182,3,6,1"),
        ])
        assert _query(conn, 6) == "1"

    def test_no_reply_returns_none(self):
        conn = _make_conn([])
        with patch("bt747cli.protocol.QUERY_TIMEOUT", 0.05):
            assert _query(conn, 6) is None


class TestQueryRecMethod:
    def test_overlap(self):
        conn = _make_conn([_sentence("PMTK182,3,6,1")])
        assert _query_rec_method(conn) == REC_METHOD_OVERLAP

    def test_stop(self):
        conn = _make_conn([_sentence("PMTK182,3,6,2")])
        assert _query_rec_method(conn) == REC_METHOD_STOP

    def test_garbage_returns_none(self):
        conn = _make_conn([_sentence("PMTK182,3,6,xyz")])
        assert _query_rec_method(conn) is None


class TestComputeEndAddr:
    FLASH = 8 * 1024 * 1024

    def test_stop_mode_downloads_up_to_write_pointer(self):
        assert _compute_end_addr(0x1234, self.FLASH, REC_METHOD_STOP) == 0x10000

    def test_stop_mode_rounds_up_to_next_sector(self):
        assert _compute_end_addr(0x10001, self.FLASH, REC_METHOD_STOP) == 0x20000

    def test_overlap_mode_downloads_full_flash(self):
        # Ring buffer may have wrapped: write pointer says nothing about extent.
        assert _compute_end_addr(0x1234, self.FLASH, REC_METHOD_OVERLAP) == self.FLASH

    def test_overlap_mode_large_write_ptr_still_full_flash(self):
        # This is the data-loss case the old heuristic got wrong.
        assert _compute_end_addr(0x600000, self.FLASH, REC_METHOD_OVERLAP) == self.FLASH

    def test_unknown_method_downloads_full_flash(self):
        assert _compute_end_addr(0x1234, self.FLASH, None) == self.FLASH

    def test_stop_mode_capped_at_flash_size(self):
        assert _compute_end_addr(self.FLASH - 1, self.FLASH, REC_METHOD_STOP) == self.FLASH
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_protocol.py -v`
Expected: ERROR with `ImportError: cannot import name 'REC_METHOD_OVERLAP'`.

- [ ] **Step 3: Implement in `bt747cli/protocol.py`**

Add constants after `QUERY_TIMEOUT` (line 41):

```python
# Recording method when the log is full (reply to $PMTK182,2,6).
# Verified against BT747 MtkModel.java:492.
REC_METHOD_OVERLAP = 1  # ring buffer: oldest data is overwritten
REC_METHOD_STOP = 2     # logging stops when flash is full
```

Add the generic query helper after `_wakeup`:

```python
def _query(conn: SerialConnection, param: int) -> str | None:
    """Send $PMTK182,2,<param> and return the value field of the reply.

    The reply has the form $PMTK182,3,<param>,<value>*CS.
    Returns the raw value string, or None on timeout/malformed reply.
    """
    log.debug("Querying $PMTK182,2,%d …", param)
    conn.send_command(f"PMTK182,2,{param}")
    prefix = f"$PMTK182,3,{param},"
    deadline = time.monotonic() + QUERY_TIMEOUT
    while time.monotonic() < deadline:
        line = conn.read_line()
        if not line:
            continue
        log.debug("rx: %s", line)
        if not line.startswith(prefix):
            continue
        body = line[1:].rsplit("*", 1)[0]
        parts = body.split(",")
        if len(parts) < 4:
            log.error("Malformed $PMTK182,3,%d reply: %s", param, line)
            return None
        return parts[3]
    log.error("No response to $PMTK182,2,%d query.", param)
    return None
```

Replace the bodies of `_query_log_size` (lines 78-108) and `_query_flash_size` (lines 129-152) to use the helper (keep their docstrings, adjust wording as needed):

```python
def _query_log_size(conn: SerialConnection) -> int | None:
    """Query the next-write-address via $PMTK182,2,7.

    Returns the byte offset of the current write pointer, or None on failure.
    """
    value = _query(conn, 7)
    if value is None:
        return None
    try:
        size = int(value, 16)
    except ValueError:
        log.error("Could not parse log write pointer from '%s'.", value)
        return None
    log.info("Log write pointer: 0x%X (%d bytes).", size, size)
    return size


def _query_flash_size(conn: SerialConnection) -> int:
    """Query the flash chip ID via $PMTK182,2,9 and return flash size in bytes.

    Returns the decoded flash size, or a default of 8 MiB on failure.
    """
    value = _query(conn, 9)
    if value is None:
        log.warning("No response to $PMTK182,2,9 – using 8 MiB default.")
        return 8 * 1024 * 1024
    try:
        flash_id = int(value, 16)
    except ValueError:
        log.error("Could not parse flash ID from '%s' – using 8 MiB default.", value)
        return 8 * 1024 * 1024
    return _flash_size_from_id(flash_id)
```

Add the new query + decision functions after `_query_flash_size`:

```python
def _query_rec_method(conn: SerialConnection) -> int | None:
    """Query the log-full recording method via $PMTK182,2,6.

    Returns REC_METHOD_OVERLAP, REC_METHOD_STOP, or None when unknown.
    """
    value = _query(conn, 6)
    if value is None:
        return None
    try:
        method = int(value)
    except ValueError:
        log.error("Could not parse recording method from '%s'.", value)
        return None
    names = {REC_METHOD_OVERLAP: "OVERLAP", REC_METHOD_STOP: "STOP"}
    log.info("Recording method: %d (%s).", method, names.get(method, "unknown"))
    return method


def _compute_end_addr(write_ptr: int, flash_size: int, rec_method: int | None) -> int:
    """Determine how many bytes of flash to download.

    Mirrors BT747 Controller.startDefaultDownload(): in STOP mode the log
    never wraps, so downloading up to the write pointer (rounded up to a full
    0x10000 sector) is sufficient.  In OVERLAP mode — or when the recording
    method is unknown — the ring buffer may have wrapped and the full flash
    must be downloaded so the oldest data is not lost.
    """
    if rec_method == REC_METHOD_STOP and write_ptr < flash_size:
        return min((write_ptr + 0xFFFF) & ~0xFFFF, flash_size)
    return flash_size
```

In `download_log`, replace the heuristic block (lines 254-267, from the comment `# If the write pointer is larger …` through the `log.info(...)` call) with:

```python
    rec_method = _query_rec_method(conn)
    end_addr = _compute_end_addr(write_ptr, flash_size, rec_method)
    log.info(
        "Write pointer: 0x%X, flash: 0x%X, rec method: %s → downloading 0x%X bytes.",
        write_ptr, flash_size, rec_method, end_addr,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_protocol.py -v`
Expected: all pass (12 new tests added by this task).

- [ ] **Step 5: Run the full suite**

Run: `venv/bin/python -m pytest tests/ -v`
Expected: 73 passed, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add bt747cli/protocol.py tests/test_protocol.py
git commit -m "fix(protocol): decide download size via recording method, not write-ptr heuristic"
```

---

### Task 3: Separate serial read timeout from overall download timeout

The CLI currently passes `--timeout` (default 300 s) as the **per-read** serial timeout, so a silent device blocks a single `read_line()` for 5 minutes and the 5-second query deadlines never fire. Fix: serial read timeout becomes a short constant (1 s), and `--timeout` becomes an overall deadline enforced inside `download_log` (this also gives the previously dead `DOWNLOAD_TIMEOUT` constant a purpose as the default).

**Files:**
- Modify: `bt747cli/connection.py:37` (default read timeout), `bt747cli/protocol.py` (`download_log` signature + deadline), `bt747cli/cli.py:153,214` (stop passing `--timeout` to `SerialConnection`, pass it to `download_log`)
- Test: `tests/test_protocol.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_protocol.py` (uses `_sentence`, `_make_conn`, `_make_pmtk182_8` from earlier; add `download_log` to the protocol import added in Task 2):

```python
class TestDownloadLog:
    """End-to-end download_log flow against a scripted mock device."""

    def _device_lines(self, write_ptr: int, rec_method: int, chunk_hex: str) -> list[str]:
        return [
            _sentence("PMTK001,0,3"),                   # wakeup ACK
            _sentence("PMTK182,3,9,C2201615"),          # flash ID → 4 MiB (1 << 0x16)
            _sentence(f"PMTK182,3,7,{write_ptr:08X}"),  # write pointer
            _sentence(f"PMTK182,3,6,{rec_method}"),     # recording method
            _make_pmtk182_8(0, chunk_hex),              # first chunk (short read)
        ]

    def test_short_read_ends_download(self):
        conn = _make_conn(self._device_lines(0x100, 2, "AABBCCDD"))
        raw = download_log(conn, timeout=30.0)
        assert raw == bytes.fromhex("AABBCCDD")

    def test_expired_timeout_aborts_before_first_chunk(self):
        conn = _make_conn(self._device_lines(0x100, 2, "AABBCCDD"))
        raw = download_log(conn, timeout=0.0)
        assert raw == b""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_protocol.py::TestDownloadLog -v`
Expected: both tests FAIL with `TypeError: download_log() got an unexpected keyword argument 'timeout'` — the kwarg does not exist yet.

- [ ] **Step 3: Implement**

`bt747cli/connection.py` line 37 — shorten the default per-read timeout so query deadlines stay responsive:

```python
    def __init__(self, port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD, timeout: float = 1.0):
```

`bt747cli/protocol.py` — change the `download_log` signature and add the deadline check at the top of the chunk loop:

```python
def download_log(
    conn: SerialConnection,
    progress_callback=None,
    timeout: float = DOWNLOAD_TIMEOUT,
) -> bytes:
```

(keep the docstring; add to its Args section: `timeout: Overall deadline for the whole download in seconds.`)

Inside `download_log`, right before the `while addr < end_addr:` loop, add:

```python
    overall_deadline = time.monotonic() + timeout
```

and as the **first** statement inside the loop:

```python
        if time.monotonic() >= overall_deadline:
            log.error(
                "Download timeout (%.0f s) exceeded at addr 0x%08X – aborting.",
                timeout, addr,
            )
            break
```

`bt747cli/cli.py` — in `cmd_download` (line 153) and `cmd_run` (line 214), stop routing `--timeout` into the serial read timeout and pass it as the download deadline instead:

```python
    with SerialConnection(port=port, baud=baud) as conn:
        raw = download_log(conn, progress_callback=_progress_echo, timeout=timeout)
```

(The `--timeout` help text "Download timeout in seconds." is now actually true.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_protocol.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `venv/bin/python -m pytest tests/ -v`
Expected: 75 passed, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add bt747cli/connection.py bt747cli/protocol.py bt747cli/cli.py tests/test_protocol.py
git commit -m "fix(protocol): enforce overall download deadline, decouple serial read timeout"
```

---

### Task 4: Harden `run.sh`

Unquoted `$@` breaks arguments containing spaces (e.g. `--track-name "My Track"`); without `set -e` failures exit 0; `source venv/bin/activate` breaks when called from another cwd. Rewrite per the bash conventions (env-bash shebang, `set -euo pipefail`, quoted expansion).

**Files:**
- Modify: `run.sh` (full rewrite)

- [ ] **Step 1: Rewrite `run.sh`**

Replace the entire file content with:

```bash
#!/usr/bin/env bash
# Convenience wrapper: run bt747cli from the project venv.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck disable=SC1091
source venv/bin/activate

if [[ $# -eq 0 ]]; then
  DATE=$(date +'%Y-%m-%d')
  bt747cli --help
  echo
  echo "e.g.:  $0 download --port /dev/ttyACM0 --output raw_qstarz2.bin"
  echo "e.g.:  $0 export --input raw_qstarz2.bin --output tracks/ --split-days"
  echo "e.g.:  $0 export --input raw_qstarz2.bin --output tracks/${DATE}.gpx --from ${DATE}"
  echo "e.g.:  $0 run --port /dev/ttyACM0 --save-bin raw_qstarz2.bin --output tracks/ --split-days"
  exit 2
fi

exec bt747cli "$@"
```

- [ ] **Step 2: Verify syntax and behavior**

```bash
bash -n run.sh                                   # expected: no output (syntax OK)
./run.sh; echo "exit=$?"                         # expected: help + examples, exit=2
./run.sh export --help >/dev/null; echo "exit=$?"  # expected: exit=0
command -v shellcheck >/dev/null && shellcheck run.sh || echo "shellcheck not installed – skipped"
```

Expected: no shellcheck errors (or skipped message).

- [ ] **Step 3: Commit**

```bash
git add run.sh
git commit -m "fix(run.sh): quote args, fail fast, exec into venv CLI"
```

---

### Task 5: Final verification and wrap-up

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `venv/bin/python -m pytest tests/ -v`
Expected: 75 passed, 0 failed, 0 skipped.

- [ ] **Step 2: Smoke-test the CLI without a device**

```bash
venv/bin/bt747cli --help
venv/bin/bt747cli export --help
```

Expected: help output, exit 0.

- [ ] **Step 3: Review the branch diff**

```bash
git log --oneline main..HEAD
git diff main..HEAD --stat
```

Expected: 5 commits (plan + 4 fixes), touching only `cli.py`, `protocol.py`, `connection.py`, `run.sh`, `tests/test_cli.py`, `tests/test_protocol.py`, and the plan file.

- [ ] **Step 4: Update the project wiki log**

Invoke the `/log-obsidian` skill to append this session's work (4 review fixes, branch `fix/review-findings`) to `~/Documents/Obsidian/second_brain/02 Projects/BT747/log.md`. If the skill is unavailable, tell the user instead of writing the file manually.

- [ ] **Step 5: Report to the user**

Do NOT merge into `main` and do NOT push — per the user's git rules there are no direct pushes to main. Report: branch name, commit list, test results, and that the next step (merge/PR) is the user's call.
