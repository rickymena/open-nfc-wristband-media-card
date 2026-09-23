"""Tag identification and write-bound tests. No hardware required."""

import unittest

from wristband.tags import (
    FIRST_USER_PAGE,
    blank_cc,
    card_name_from_atr,
    identify,
    parse_cc,
)

# A real PC/SC storage-card ATR for a MIFARE Ultralight family tag.
ATR_UL = bytes.fromhex("3B8F8001804F0CA0000003060300030000000068")
CC_NTAG213 = bytes([0xE1, 0x10, 0x12, 0x00])
CC_NTAG215 = bytes([0xE1, 0x10, 0x3E, 0x00])
CC_NTAG216 = bytes([0xE1, 0x10, 0x6D, 0x00])


class TestAtr(unittest.TestCase):
    def test_ultralight_family(self):
        self.assertEqual(card_name_from_atr(ATR_UL), "MIFARE Ultralight")

    def test_non_storage_atr(self):
        self.assertIn("unknown", card_name_from_atr(b"\x3b\x00"))

    def test_truncated_atr(self):
        self.assertIn("unknown", card_name_from_atr(bytes.fromhex("3B8FA000000306")))


class TestCapabilityContainer(unittest.TestCase):
    def test_ntag213(self):
        product, capacity, formatted, writable = parse_cc(CC_NTAG213)
        self.assertEqual(product, "NTAG213")
        self.assertEqual(capacity, 144)
        self.assertTrue(formatted and writable)

    def test_ntag215_and_216(self):
        self.assertEqual(parse_cc(CC_NTAG215)[1], 504)
        self.assertEqual(parse_cc(CC_NTAG216)[1], 888)

    def test_unformatted(self):
        self.assertEqual(parse_cc(b"\x00\x00\x00\x00"), ("unformatted", 0, False, True))

    def test_read_only_flag(self):
        self.assertFalse(parse_cc(bytes([0xE1, 0x10, 0x12, 0x0F]))[3])

    def test_unknown_size_falls_back_to_arithmetic(self):
        product, capacity, formatted, _ = parse_cc(bytes([0xE1, 0x10, 0x20, 0x00]))
        self.assertTrue(formatted)
        self.assertEqual(capacity, 0x20 * 8)
        self.assertIn("unknown", product)

    def test_blank_cc_round_trips(self):
        self.assertEqual(parse_cc(blank_cc(144))[1], 144)
        self.assertEqual(parse_cc(blank_cc(504))[1], 504)


class TestWriteBounds(unittest.TestCase):
    def test_ntag213_last_page_is_39(self):
        # NTAG213 user memory is pages 4..39. Writing past 39 hits the dynamic
        # lock bytes and config pages, which can brick the tag.
        info = identify(ATR_UL, b"\x04\x01\x02\x03\x04\x05\x06", CC_NTAG213)
        self.assertEqual(info.last_user_page, 39)
        self.assertEqual(info.last_user_page - FIRST_USER_PAGE + 1, 36)

    def test_ntag215_last_page_is_129(self):
        info = identify(ATR_UL, b"", CC_NTAG215)
        self.assertEqual(info.last_user_page, 129)

    def test_ntag216_last_page_is_225(self):
        info = identify(ATR_UL, b"", CC_NTAG216)
        self.assertEqual(info.last_user_page, 225)

    def test_identify_without_cc(self):
        info = identify(ATR_UL, b"\x04\x01", None)
        self.assertFalse(info.formatted)
        self.assertEqual(info.product, "MIFARE Ultralight")

    def test_describe_mentions_lock_state(self):
        info = identify(ATR_UL, b"\x04\x01", bytes([0xE1, 0x10, 0x12, 0x0F]))
        self.assertIn("READ-ONLY", info.describe())


if __name__ == "__main__":
    unittest.main()
