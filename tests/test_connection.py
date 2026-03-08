"""Tests for connection.py: PMTK checksum and sentence building."""

import pytest
from bt747cli.connection import build_pmtk, _pmtk_checksum


class TestPmtkChecksum:
    def test_known_value(self):
        # $PMTK000*32  – test sentence with known checksum
        assert _pmtk_checksum("PMTK000") == "32"

    def test_download_command(self):
        # $PMTK622,1*29  – actual download command
        assert _pmtk_checksum("PMTK622,1") == "29"

    def test_empty_payload(self):
        # XOR of zero bytes = 0
        assert _pmtk_checksum("") == "00"

    def test_single_char(self):
        assert _pmtk_checksum("A") == f"{ord('A'):02X}"


class TestBuildPmtk:
    def test_ends_with_crlf(self):
        sentence = build_pmtk("PMTK000")
        assert sentence.endswith(b"\r\n")

    def test_starts_with_dollar(self):
        sentence = build_pmtk("PMTK000")
        assert sentence.startswith(b"$")

    def test_checksum_in_sentence(self):
        sentence = build_pmtk("PMTK622,1")
        assert b"*29" in sentence

    def test_full_sentence(self):
        sentence = build_pmtk("PMTK622,1")
        assert sentence == b"$PMTK622,1*29\r\n"
