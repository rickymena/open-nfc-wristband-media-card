"""write_ndef against soft- and hard-locked tags, using an in-memory NTAG213."""

import unittest

from wristband.tags import identify
from wristband.writer import build_url_payload, hard_locked, write_ndef

ATR_UL = bytes.fromhex("3B8F8001804F0CA0000003060300030000000068")
UID = bytes.fromhex("047F62DACA2090")


class FakeTag:
    """45 pages of NTAG213 memory with a CC and an empty NDEF TLV."""

    def __init__(self, cc_access=0x00, static=(0, 0), dynamic=(0, 0)):
        self.mem = bytearray(45 * 4)
        self.mem[8:12] = bytes([0, 0, static[0], static[1]])
        self.mem[12:16] = bytes([0xE1, 0x10, 0x12, cc_access])
        self.mem[16:19] = bytes([0x03, 0x00, 0xFE])
        self.mem[0x28 * 4 : 0x28 * 4 + 2] = bytes(dynamic)

    def info(self):
        return identify(ATR_UL, UID, bytes(self.mem[12:16]))

    def read_pages(self, start, count):
        return bytes(self.mem[start * 4 : (start + count) * 4])

    def write_page(self, page, data):
        self.mem[page * 4 : page * 4 + 4] = data


PAYLOAD = build_url_payload("https://example.com/pete")


class TestSoftLock(unittest.TestCase):
    def test_refused_without_force(self):
        tag = FakeTag(cc_access=0x0F)
        result = write_ndef(tag, tag.info(), PAYLOAD)
        self.assertFalse(result.ok)
        self.assertIn("force", result.message)

    def test_force_rewrites_data(self):
        tag = FakeTag(cc_access=0x0F)
        result = write_ndef(tag, tag.info(), PAYLOAD, force=True)
        self.assertTrue(result.ok, result.message)
        self.assertIn(PAYLOAD, bytes(tag.mem))
        self.assertEqual(tag.mem[15], 0x0F, "CC must be left read-only")


class TestHardLock(unittest.TestCase):
    def test_static_lock_bits_refuse_force(self):
        tag = FakeTag(cc_access=0x0F, static=(0xF8, 0xFF))
        self.assertTrue(hard_locked(tag, tag.info()))
        self.assertFalse(write_ndef(tag, tag.info(), PAYLOAD, force=True).ok)

    def test_dynamic_lock_bits_refuse_force(self):
        tag = FakeTag(cc_access=0x0F, dynamic=(0x01, 0x00))
        self.assertTrue(hard_locked(tag, tag.info()))
        self.assertFalse(write_ndef(tag, tag.info(), PAYLOAD, force=True).ok)

    def test_block_locking_bits_alone_are_not_a_lock(self):
        tag = FakeTag(static=(0x07, 0x00))
        self.assertFalse(hard_locked(tag, tag.info()))


if __name__ == "__main__":
    unittest.main()
