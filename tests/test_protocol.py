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
