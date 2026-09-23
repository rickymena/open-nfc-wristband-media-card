"""Lock bit mask tests, checked against the NXP NTAG213/215/216 datasheet
rev 3.2, sections 8.5.2 and 8.5.3 (figures 9-12).

These masks are written to hardware irreversibly, so the bit-to-page mapping
is re-derived here from the datasheet's stated granularity rather than just
asserting the constants back at themselves.
"""

import unittest

from wristband.tags import (
    CC_READ_ONLY,
    DYNAMIC_LOCK,
    STATIC_LOCK_MASK,
    STATIC_LOCK_PAGE,
    identify,
    parse_cc,
)

ATR_UL = bytes.fromhex("3B8F8001804F0CA0000003060300030000000068")

# (product, CC, granularity in pages, last user page) per the datasheet.
CHIPS = [
    ("NTAG213", bytes([0xE1, 0x10, 0x12, 0x00]), 2, 39),
    ("NTAG215", bytes([0xE1, 0x10, 0x3E, 0x00]), 16, 129),
    ("NTAG216", bytes([0xE1, 0x10, 0x6D, 0x00]), 16, 225),
]


def pages_covered(byte0: int, byte1: int, granularity: int) -> int:
    """Highest page covered by a dynamic lock mask.

    Lock bits run from page 16 upwards, byte 0 bit 0 first, each bit covering
    `granularity` pages.
    """
    combined = byte0 | (byte1 << 8)
    span = 0
    for i in range(16):
        if combined & (1 << i):
            span = i + 1
    return 16 + span * granularity - 1


class TestStaticLock(unittest.TestCase):
    def test_lives_on_page_2(self):
        self.assertEqual(STATIC_LOCK_PAGE, 2)

    def test_block_locking_bits_left_clear(self):
        # Byte 2 bits 0-2 are BL-CC, BL9-4 and BL15-10. Setting them freezes
        # the lock configuration and gains nothing once every Lx is set.
        self.assertEqual(STATIC_LOCK_MASK[0] & 0b111, 0)

    def test_locks_cc_and_pages_4_through_7(self):
        # Byte 2: bit3 = L-CC, bits 4-7 = L4..L7.
        self.assertEqual(STATIC_LOCK_MASK[0] & 0b1000, 0b1000, "L-CC must be set")
        self.assertEqual(STATIC_LOCK_MASK[0] >> 4, 0b1111, "L4..L7 must be set")

    def test_locks_pages_8_through_15(self):
        self.assertEqual(STATIC_LOCK_MASK[1], 0xFF)


class TestDynamicLock(unittest.TestCase):
    def test_every_ntag_has_an_entry(self):
        for product, _, _, _ in CHIPS:
            self.assertIn(product, DYNAMIC_LOCK)

    def test_lock_pages_match_datasheet(self):
        for product, page in (("NTAG213", 0x28), ("NTAG215", 0x82), ("NTAG216", 0xE2)):
            with self.subTest(product=product):
                self.assertEqual(DYNAMIC_LOCK[product][0], page)

    def test_masks_cover_all_user_memory(self):
        for product, cc, granularity, last_page in CHIPS:
            with self.subTest(product=product):
                _, b0, b1 = DYNAMIC_LOCK[product]
                covered = pages_covered(b0, b1, granularity)
                self.assertGreaterEqual(
                    covered, last_page,
                    f"{product}: mask covers to page {covered}, user memory ends at {last_page}",
                )

    def test_masks_do_not_set_rfui_bits(self):
        # NTAG213 byte1 bits 4-7 are RFUI; NTAG215 byte1 is entirely RFUI;
        # NTAG216 byte1 bits 6-7 are RFUI. The datasheet says write them as 0.
        self.assertEqual(DYNAMIC_LOCK["NTAG213"][2] & 0xF0, 0)
        self.assertEqual(DYNAMIC_LOCK["NTAG215"][2], 0x00)
        self.assertEqual(DYNAMIC_LOCK["NTAG216"][2] & 0xC0, 0)

    def test_ntag213_covers_exactly_pages_16_to_39(self):
        _, b0, b1 = DYNAMIC_LOCK["NTAG213"]
        self.assertEqual(b0, 0xFF)   # pages 16-31, two per bit
        self.assertEqual(b1, 0x0F)   # pages 32-39, two per bit
        self.assertEqual(pages_covered(b0, b1, 2), 39)

    def test_known_partial_mask_matches_published_example(self):
        # NXP community guidance: writing 03 00 00 locks pages 16-19.
        self.assertEqual(pages_covered(0x03, 0x00, 2), 19)


class TestCcReadOnly(unittest.TestCase):
    def test_write_forbidden_read_free(self):
        self.assertEqual(CC_READ_ONLY & 0x0F, 0x0F, "write access must be forbidden")
        self.assertEqual(CC_READ_ONLY >> 4, 0x00, "read access must stay free")

    def test_parse_cc_reports_locked_after_or(self):
        cc = bytearray([0xE1, 0x10, 0x12, 0x00])
        cc[3] |= CC_READ_ONLY
        self.assertFalse(parse_cc(bytes(cc))[3])

    def test_capacity_survives_the_lock(self):
        cc = bytes([0xE1, 0x10, 0x12, CC_READ_ONLY])
        info = identify(ATR_UL, b"", cc)
        self.assertEqual(info.product, "NTAG213")
        self.assertEqual(info.capacity, 144)
        self.assertFalse(info.writable)


if __name__ == "__main__":
    unittest.main()
