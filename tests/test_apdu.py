"""APDU construction tests, including the AK9567 16-byte write quirk.

Runs without pyscard or a reader attached -- that is why the builders live in
apdu.py rather than inside the pyscard-importing reader module.
"""

import unittest

from wristband.apdu import (
    PAGE_SIZE,
    WRITE_BLOCK,
    explain,
    get_uid,
    read_binary,
    update_binary_padded,
    update_binary_plain,
)


class TestBuilders(unittest.TestCase):
    def test_get_uid(self):
        self.assertEqual(get_uid(), [0xFF, 0xCA, 0x00, 0x00, 0x00])

    def test_read_binary(self):
        self.assertEqual(read_binary(0x04, 16), [0xFF, 0xB0, 0x00, 0x04, 0x10])

    def test_plain_write(self):
        apdu = update_binary_plain(5, bytes([0x34, 0x03, 0x3C, 0xD1]))
        self.assertEqual(apdu, [0xFF, 0xD6, 0x00, 0x05, 0x04, 0x34, 0x03, 0x3C, 0xD1])

    def test_plain_write_rejects_wrong_size(self):
        for bad in (b"", b"\x01\x02\x03", b"\x01\x02\x03\x04\x05"):
            with self.subTest(n=len(bad)), self.assertRaises(ValueError):
                update_binary_plain(5, bad)


class TestPaddedWrite(unittest.TestCase):
    """The Alcor Link AK9567 rejects Lc=04 and needs a 16-byte APDU."""

    def test_length_byte_is_16(self):
        apdu = update_binary_padded(5, b"\xde\xad\xbe\xef")
        self.assertEqual(apdu[4], WRITE_BLOCK)
        self.assertEqual(len(apdu) - 5, WRITE_BLOCK)

    def test_target_page_occupies_first_four_bytes(self):
        apdu = update_binary_padded(5, b"\xde\xad\xbe\xef")
        self.assertEqual(apdu[5:9], [0xDE, 0xAD, 0xBE, 0xEF])

    def test_context_fills_the_padding(self):
        # A reader that honours all 16 bytes must rewrite the next three pages
        # with what they already hold, not with zeros.
        context = bytes(range(16))
        apdu = update_binary_padded(8, b"\xaa\xbb\xcc\xdd", context)
        self.assertEqual(apdu[5:9], [0xAA, 0xBB, 0xCC, 0xDD])
        self.assertEqual(bytes(apdu[9:]), context[PAGE_SIZE:])

    def test_missing_context_is_zero_filled(self):
        apdu = update_binary_padded(8, b"\xaa\xbb\xcc\xdd")
        self.assertEqual(apdu[9:], [0] * (WRITE_BLOCK - PAGE_SIZE))

    def test_short_context_is_padded_not_truncated(self):
        apdu = update_binary_padded(8, b"\xaa\xbb\xcc\xdd", b"\x01" * 6)
        self.assertEqual(len(apdu) - 5, WRITE_BLOCK)
        # 6 bytes of context, 4 of which the new page replaces, leaves 2.
        self.assertEqual(apdu[9:11], [0x01, 0x01])
        self.assertEqual(apdu[11:], [0x00] * 10)

    def test_oversized_context_is_clipped(self):
        apdu = update_binary_padded(8, b"\xaa\xbb\xcc\xdd", bytes(64))
        self.assertEqual(len(apdu) - 5, WRITE_BLOCK)

    def test_matches_apdu_that_worked_on_hardware(self):
        # Verified against the real reader: this wrote page 20 and left pages
        # 21-23 untouched.
        apdu = update_binary_padded(0x14, bytes([0x11, 0x22, 0x33, 0x44]))
        self.assertEqual(apdu[:5], [0xFF, 0xD6, 0x00, 0x14, 0x10])


class TestExplain(unittest.TestCase):
    def test_6300_mentions_the_write_form(self):
        self.assertIn("16-byte write form", explain(0x63, 0x00))

    def test_6a81_unsupported(self):
        self.assertIn("does not support", explain(0x6A, 0x81))

    def test_success_has_no_note(self):
        self.assertEqual(explain(0x90, 0x00), "")
