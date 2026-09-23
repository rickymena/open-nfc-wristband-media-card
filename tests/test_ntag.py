"""NTAG21x configuration page tests.

Bit layouts from the NXP NTAG213/215/216 data sheet rev 3.2, section 8.5.7,
tables 8-11. These bytes change chip behaviour permanently in several cases,
so the encoding is checked against the datasheet's stated bit positions and
against bytes read off real tags.
"""

import unittest

from wristband.ntag import (
    AUTH0_DISABLED,
    CONFIG_PAGE,
    MIRROR_BOTH,
    MIRROR_COUNTER,
    MIRROR_NONE,
    MIRROR_UID,
    MIRROR_WIDTH,
    Config,
    build_access_byte,
    build_config_pages,
    build_mirror_byte,
    describe,
    mirror_fits,
    parse_config,
    protection_locks_out,
)

# Pages 41-44 read from a factory NTAG213 wristband and sticker. Identical on
# both: no mirror, strong modulation on, AUTH0 disabled, no protection.
FACTORY = bytes.fromhex("04 00 00 FF  00 05 00 00  00 00 00 00  00 00 00 00".replace(" ", ""))

NTAG213_LAST_USER_PAGE = 39


class TestConfigPages(unittest.TestCase):
    def test_config_page_addresses(self):
        # Datasheet table 8: 29h / 83h / E3h.
        self.assertEqual(CONFIG_PAGE["NTAG213"], 0x29)
        self.assertEqual(CONFIG_PAGE["NTAG215"], 0x83)
        self.assertEqual(CONFIG_PAGE["NTAG216"], 0xE3)

    def test_parses_real_factory_bytes(self):
        cfg = parse_config(FACTORY)
        self.assertEqual(cfg.mirror_conf, MIRROR_NONE)
        self.assertEqual(cfg.auth0, 0xFF)
        self.assertFalse(cfg.prot)
        self.assertFalse(cfg.cfglck)
        self.assertFalse(cfg.nfc_cnt_en)
        self.assertEqual(cfg.authlim, 0)

    def test_factory_has_strong_modulation_on(self):
        # MIRROR byte 0x04 = bit 2 set = STRG_MOD_EN.
        self.assertTrue(parse_config(FACTORY).strong_modulation)

    def test_factory_protection_is_inactive(self):
        self.assertFalse(parse_config(FACTORY).protection_active(NTAG213_LAST_USER_PAGE))

    def test_round_trip_preserves_factory_bytes(self):
        cfg = parse_config(FACTORY)
        p41, p42 = build_config_pages(cfg, FACTORY)
        self.assertEqual(p41, FACTORY[0:4], "page 41 changed on a no-op rebuild")
        self.assertEqual(p42, FACTORY[4:8], "page 42 changed on a no-op rebuild")

    def test_rfui_preserved_from_current(self):
        cfg = parse_config(FACTORY)
        _, p42 = build_config_pages(cfg, FACTORY)
        self.assertEqual(p42[1], 0x05, "factory RFUI byte must survive")

    def test_rfui_zeroed_when_current_unknown(self):
        _, p42 = build_config_pages(parse_config(FACTORY))
        self.assertEqual(p42[1:], b"\x00\x00\x00")

    def test_short_input_rejected(self):
        with self.assertRaises(ValueError):
            parse_config(b"\x00" * 8)


class TestMirrorByte(unittest.TestCase):
    def test_mode_is_top_two_bits(self):
        for mode in (MIRROR_NONE, MIRROR_UID, MIRROR_COUNTER, MIRROR_BOTH):
            with self.subTest(mode=mode):
                b = build_mirror_byte(Config(mirror_conf=mode))
                self.assertEqual(b >> 6, mode)

    def test_byte_offset_is_bits_5_4(self):
        for off in range(4):
            with self.subTest(off=off):
                b = build_mirror_byte(Config(mirror_byte=off))
                self.assertEqual((b >> 4) & 0x03, off)

    def test_strong_modulation_is_bit_2(self):
        self.assertEqual(build_mirror_byte(Config(strong_modulation=True)) & 0x04, 0x04)
        self.assertEqual(build_mirror_byte(Config(strong_modulation=False)) & 0x04, 0)

    def test_rfui_bits_stay_clear(self):
        b = build_mirror_byte(Config(mirror_conf=MIRROR_BOTH, mirror_byte=3,
                                     strong_modulation=True))
        self.assertEqual(b & 0x0B, 0, "bits 3, 1 and 0 are RFUI and must be 0")

    def test_round_trip(self):
        cfg = Config(mirror_conf=MIRROR_BOTH, mirror_byte=2, strong_modulation=True,
                     mirror_page=10)
        p41, p42 = build_config_pages(cfg)
        back = parse_config(p41 + p42 + bytes(8))
        self.assertEqual(back.mirror_conf, MIRROR_BOTH)
        self.assertEqual(back.mirror_byte, 2)
        self.assertTrue(back.strong_modulation)
        self.assertEqual(back.mirror_page, 10)

    def test_mirror_needs_page_above_cc(self):
        self.assertFalse(Config(mirror_conf=MIRROR_UID, mirror_page=0x03).mirror_enabled())
        self.assertTrue(Config(mirror_conf=MIRROR_UID, mirror_page=0x04).mirror_enabled())
        self.assertFalse(Config(mirror_conf=MIRROR_NONE, mirror_page=0x10).mirror_enabled())

    def test_mirror_widths(self):
        # 7-byte UID -> 14 hex chars; 3-byte counter -> 6; both -> 21 with separator.
        self.assertEqual(MIRROR_WIDTH[MIRROR_UID], 14)
        self.assertEqual(MIRROR_WIDTH[MIRROR_COUNTER], 6)
        self.assertEqual(MIRROR_WIDTH[MIRROR_BOTH], 21)

    def test_mirror_must_fit_in_user_memory(self):
        self.assertTrue(mirror_fits(4, 0, MIRROR_UID, NTAG213_LAST_USER_PAGE))
        self.assertTrue(mirror_fits(36, 2, MIRROR_UID, NTAG213_LAST_USER_PAGE))
        self.assertFalse(mirror_fits(39, 0, MIRROR_UID, NTAG213_LAST_USER_PAGE))
        self.assertTrue(mirror_fits(39, 0, MIRROR_NONE, NTAG213_LAST_USER_PAGE))


class TestAccessByte(unittest.TestCase):
    def test_bit_positions(self):
        self.assertEqual(build_access_byte(Config(prot=True)) & 0x80, 0x80)
        self.assertEqual(build_access_byte(Config(cfglck=True)) & 0x40, 0x40)
        self.assertEqual(build_access_byte(Config(nfc_cnt_en=True)) & 0x10, 0x10)
        self.assertEqual(build_access_byte(Config(nfc_cnt_pwd_prot=True)) & 0x08, 0x08)

    def test_authlim_is_low_three_bits(self):
        for n in range(8):
            with self.subTest(n=n):
                self.assertEqual(build_access_byte(Config(authlim=n)) & 0x07, n)

    def test_bit_5_is_rfui_and_stays_clear(self):
        b = build_access_byte(Config(prot=True, cfglck=True, nfc_cnt_en=True,
                                     nfc_cnt_pwd_prot=True, authlim=7))
        self.assertEqual(b & 0x20, 0)

    def test_round_trip(self):
        cfg = Config(prot=True, nfc_cnt_en=True, authlim=5)
        _, p42 = build_config_pages(cfg)
        back = parse_config(bytes(4) + p42 + bytes(8))
        self.assertTrue(back.prot)
        self.assertTrue(back.nfc_cnt_en)
        self.assertEqual(back.authlim, 5)
        self.assertFalse(back.cfglck)


class TestLockoutGuard(unittest.TestCase):
    """The guard that stops a reader enabling protection it cannot undo."""

    def test_factory_config_is_safe(self):
        cfg = parse_config(FACTORY)
        self.assertIsNone(protection_locks_out(cfg, NTAG213_LAST_USER_PAGE, False))

    def test_protection_without_auth_is_refused(self):
        cfg = Config(auth0=4)
        msg = protection_locks_out(cfg, NTAG213_LAST_USER_PAGE, can_authenticate=False)
        self.assertIsNotNone(msg)
        self.assertIn("PWD_AUTH", msg)

    def test_protection_allowed_when_auth_is_possible(self):
        cfg = Config(auth0=4)
        self.assertIsNone(protection_locks_out(cfg, NTAG213_LAST_USER_PAGE, True))

    def test_auth0_above_user_memory_is_not_protection(self):
        cfg = Config(auth0=AUTH0_DISABLED)
        self.assertIsNone(protection_locks_out(cfg, NTAG213_LAST_USER_PAGE, False))
        cfg = Config(auth0=40)  # one past the last NTAG213 user page
        self.assertIsNone(protection_locks_out(cfg, NTAG213_LAST_USER_PAGE, False))

    def test_cfglck_always_refused(self):
        msg = protection_locks_out(Config(cfglck=True), NTAG213_LAST_USER_PAGE, True)
        self.assertIn("CFGLCK", msg)

    def test_authlim_always_refused(self):
        msg = protection_locks_out(Config(authlim=3), NTAG213_LAST_USER_PAGE, True)
        self.assertIn("AUTHLIM", msg)


class TestDescribe(unittest.TestCase):
    def test_factory_summary(self):
        lines = "\n".join(describe(parse_config(FACTORY), NTAG213_LAST_USER_PAGE))
        self.assertIn("off", lines)
        self.assertIn("inactive", lines)
        self.assertIn("unlimited", lines)

    def test_active_protection_is_called_out(self):
        lines = "\n".join(describe(Config(auth0=4, prot=True), NTAG213_LAST_USER_PAGE))
        self.assertIn("ACTIVE", lines)
        self.assertIn("reads and writes", lines)


if __name__ == "__main__":
    unittest.main()
