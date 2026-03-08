"""Serial connection to MTK GPS logger devices.

Handles opening the port, computing PMTK checksums, sending commands,
and reading NMEA-style response lines.
"""

import time
import serial


DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 115200


def _pmtk_checksum(payload: str) -> str:
    """Compute XOR checksum over all characters in *payload*.

    The payload is the text between '$' and '*' (exclusive) in a PMTK sentence.
    Returns a two-character uppercase hex string.
    """
    checksum = 0
    for ch in payload:
        checksum ^= ord(ch)
    return f"{checksum:02X}"


def build_pmtk(payload: str) -> bytes:
    """Wrap *payload* in a complete PMTK sentence with checksum and CRLF."""
    cs = _pmtk_checksum(payload)
    sentence = f"${payload}*{cs}\r\n"
    return sentence.encode("ascii")


class SerialConnection:
    """Manages the serial port connection to an MTK GPS logger."""

    def __init__(self, port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD, timeout: float = 5.0):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._serial: serial.Serial | None = None

    def open(self) -> None:
        """Open the serial port."""
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
        )
        # Flush any stale data
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        # Give the device time to settle after DTR/RTS toggling on open
        time.sleep(0.5)

    def close(self) -> None:
        """Close the serial port if open."""
        if self._serial and self._serial.is_open:
            self._serial.close()

    def __enter__(self) -> "SerialConnection":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def send_command(self, payload: str) -> None:
        """Build and send a PMTK command to the device."""
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("Serial port is not open")
        cmd = build_pmtk(payload)
        self._serial.write(cmd)
        self._serial.flush()

    def read_line(self) -> str:
        """Read one line from the device (strips trailing CRLF)."""
        if self._serial is None:
            raise RuntimeError("Serial port is not open")
        raw = self._serial.readline()
        return raw.decode("ascii", errors="replace").strip()

    def read_lines(self, stop_condition, timeout: float | None = None) -> list[str]:
        """Read lines until *stop_condition(lines)* returns True or *timeout* elapses.

        Args:
            stop_condition: Callable[[list[str]], bool] – evaluated after each
                new line is appended to the accumulator.
            timeout: Maximum seconds to wait; falls back to self.timeout when None.

        Returns:
            List of received lines.
        """
        if timeout is None:
            timeout = self.timeout
        lines: list[str] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.read_line()
            if line:
                lines.append(line)
                if stop_condition(lines):
                    break
        return lines
