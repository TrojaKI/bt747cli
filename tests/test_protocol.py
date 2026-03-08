"""Tests for protocol.py: PMTK sentence verification and chunk parsing."""

from unittest.mock import MagicMock, patch
import time

from bt747cli.protocol import _verify_sentence, _request_chunk
from bt747cli.connection import _pmtk_checksum


class TestVerifySentence:
    def test_valid_sentence(self):
        # $PMTK000*32
        assert _verify_sentence("$PMTK000*32") is True

    def test_invalid_checksum(self):
        assert _verify_sentence("$PMTK000*00") is False

    def test_no_dollar(self):
        assert _verify_sentence("PMTK000*32") is False

    def test_no_asterisk(self):
        assert _verify_sentence("$PMTK000") is False


def _make_pmtk182_8(addr: int, hex_data: str) -> str:
    """Build a $PMTK182,8 sentence with correct checksum."""
    payload = f"PMTK182,8,{addr:08X},{hex_data}"
    cs = _pmtk_checksum(payload)
    return f"${payload}*{cs}"


class TestRequestChunk:
    """Test _request_chunk using a mock SerialConnection."""

    def _make_conn(self, lines: list[str]):
        """Return a mock connection that yields *lines* one by one, then ''."""
        conn = MagicMock()
        it = iter(lines)
        conn.read_line.side_effect = lambda: next(it, "")
        return conn

    def test_returns_data_for_correct_address(self):
        sentence = _make_pmtk182_8(0x0000_0000, "DEADBEEF")
        conn = self._make_conn([sentence])
        result = _request_chunk(conn, 0, 4, timeout=1.0)
        assert result == bytes.fromhex("DEADBEEF")

    def test_ignores_nmea_before_data(self):
        nmea = "$GPGGA,123456.000,4800.0,N,01100.0,E,1,8,1.0,100.0,M,0.0,M,,*XX"
        sentence = _make_pmtk182_8(0x0000_0000, "AABBCCDD")
        conn = self._make_conn([nmea, sentence])
        result = _request_chunk(conn, 0, 4, timeout=1.0)
        assert result == bytes.fromhex("AABBCCDD")

    def test_ignores_wrong_address(self):
        wrong = _make_pmtk182_8(0x0000_0100, "11223344")
        correct = _make_pmtk182_8(0x0000_0000, "AABBCCDD")
        conn = self._make_conn([wrong, correct])
        result = _request_chunk(conn, 0, 4, timeout=1.0)
        assert result == bytes.fromhex("AABBCCDD")

    def test_bad_checksum_skipped(self):
        bad = "$PMTK182,8,00000000,DEADBEEF*00"  # wrong checksum
        good = _make_pmtk182_8(0, "CAFEBABE")
        conn = self._make_conn([bad, good])
        result = _request_chunk(conn, 0, 4, timeout=1.0)
        assert result == bytes.fromhex("CAFEBABE")

    def test_error_ack_returns_none(self):
        # $PMTK001,182,7,2 = ACK for sub_cmd 7 (download), result=2 (failed)
        from bt747cli.connection import _pmtk_checksum
        payload = "PMTK001,182,7,2"
        ack_fail = f"${payload}*{_pmtk_checksum(payload)}"
        conn = self._make_conn([ack_fail])
        result = _request_chunk(conn, 0, 4, timeout=0.1)
        assert result is None

    def test_unrelated_ack_is_ignored(self):
        # $PMTK001,182,2,3 = delayed ACK for size query (sub_cmd=2) – must NOT abort
        from bt747cli.connection import _pmtk_checksum
        payload = "PMTK001,182,2,3"
        unrelated_ack = f"${payload}*{_pmtk_checksum(payload)}"
        data_sentence = _make_pmtk182_8(0, "CAFECAFE")
        conn = self._make_conn([unrelated_ack, data_sentence])
        result = _request_chunk(conn, 0, 4, timeout=1.0)
        assert result == bytes.fromhex("CAFECAFE")

    def test_timeout_returns_none(self):
        conn = self._make_conn([])  # no data
        result = _request_chunk(conn, 0, 4, timeout=0.05)
        assert result is None

    def test_success_ack_after_data_is_ignored(self):
        """$PMTK001,182,7,3 (success ACK) may arrive after data – must not break parsing."""
        sentence = _make_pmtk182_8(0, "12345678")
        ack_ok = "$PMTK001,182,7,3*20"
        conn = self._make_conn([sentence, ack_ok])
        result = _request_chunk(conn, 0, 4, timeout=1.0)
        assert result == bytes.fromhex("12345678")
