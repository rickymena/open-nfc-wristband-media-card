"""NDEF round-trip and edge-case tests. No hardware required."""

import unittest

from wristband.ndef import (
    NdefError,
    Record,
    RTD_URI,
    TNF_WELL_KNOWN,
    decode_message,
    decode_text_payload,
    decode_uri_payload,
    encode_message,
    encode_uri_payload,
    text_record,
    unwrap_tlv,
    uri_record,
    wrap_tlv,
)

CARD_URL = "https://example.com/c/3b9c2f47-5d18-4e6a-9f02-7c81ad5e6b34"


class TestUriPrefix(unittest.TestCase):
    def test_https_is_abbreviated(self):
        self.assertEqual(encode_uri_payload("https://example.com")[0], 0x04)

    def test_longest_prefix_wins(self):
        # "https://www." (0x02) must beat the shorter "https://" (0x04).
        payload = encode_uri_payload("https://www.example.com")
        self.assertEqual(payload[0], 0x02)
        self.assertEqual(payload[1:], b"example.com")

    def test_unknown_scheme_uses_no_prefix(self):
        payload = encode_uri_payload("gopher://example.com")
        self.assertEqual(payload[0], 0x00)

    def test_tel_and_mailto(self):
        self.assertEqual(encode_uri_payload("tel:+15125550123")[0], 0x05)
        self.assertEqual(encode_uri_payload("mailto:a@b.co")[0], 0x06)

    def test_round_trip(self):
        for url in (
            CARD_URL,
            "http://www.example.com/x?y=1",
            "gopher://example.com",
            "tel:+15125550123",
        ):
            with self.subTest(url=url):
                self.assertEqual(decode_uri_payload(encode_uri_payload(url)), url)

    def test_rejects_bad_prefix_code(self):
        with self.assertRaises(NdefError):
            decode_uri_payload(bytes([0xFE]) + b"x")


class TestMessage(unittest.TestCase):
    def test_single_record_header(self):
        msg = encode_message([uri_record(CARD_URL)])
        # MB|ME|SR|TNF_WELL_KNOWN
        self.assertEqual(msg[0], 0xD1)
        self.assertEqual(msg[1], 1)  # type length
        self.assertEqual(msg[3:4], RTD_URI)

    def test_round_trip(self):
        records = decode_message(encode_message([uri_record(CARD_URL)]))
        self.assertEqual(len(records), 1)
        self.assertEqual(decode_uri_payload(records[0].payload), CARD_URL)

    def test_multi_record_flags(self):
        msg = encode_message([uri_record(CARD_URL), text_record("hi")])
        out = decode_message(msg)
        self.assertEqual(len(out), 2)
        self.assertEqual(msg[0] & 0x80, 0x80)  # first carries MB

    def test_long_record(self):
        big = Record(TNF_WELL_KNOWN, b"T", b"\x02en" + b"x" * 400)
        out = decode_message(encode_message([big]))
        self.assertEqual(out[0].payload, big.payload)

    def test_empty_message_rejected(self):
        with self.assertRaises(NdefError):
            encode_message([])

    def test_truncated_message_rejected(self):
        with self.assertRaises(NdefError):
            decode_message(encode_message([uri_record(CARD_URL)])[:-5])


class TestText(unittest.TestCase):
    def test_round_trip(self):
        text, lang = decode_text_payload(text_record("hola", "es").payload)
        self.assertEqual((text, lang), ("hola", "es"))

    def test_utf8_content(self):
        text, _ = decode_text_payload(text_record("café crème").payload)
        self.assertEqual(text, "café crème")


class TestTlv(unittest.TestCase):
    def test_round_trip(self):
        msg = encode_message([uri_record(CARD_URL)])
        tlv = wrap_tlv(msg)
        self.assertEqual(tlv[0], 0x03)
        self.assertEqual(tlv[-1], 0xFE)
        self.assertEqual(unwrap_tlv(tlv), msg)

    def test_skips_lock_control_tlv(self):
        msg = encode_message([uri_record(CARD_URL)])
        # A lock-control TLV (0x01, len 3) ahead of the NDEF TLV, as seen on
        # plenty of real tags.
        data = bytes([0x01, 0x03, 0xAA, 0xBB, 0xCC]) + wrap_tlv(msg)
        self.assertEqual(unwrap_tlv(data), msg)

    def test_skips_null_tlvs(self):
        msg = encode_message([uri_record(CARD_URL)])
        self.assertEqual(unwrap_tlv(b"\x00\x00" + wrap_tlv(msg)), msg)

    def test_unformatted_tag_raises(self):
        with self.assertRaises(NdefError):
            unwrap_tlv(b"\x00" * 16)

    def test_three_byte_length_form(self):
        big = encode_message([Record(TNF_WELL_KNOWN, b"T", b"\x02en" + b"y" * 400)])
        tlv = wrap_tlv(big)
        self.assertEqual(tlv[1], 0xFF)  # extended length marker
        self.assertEqual(unwrap_tlv(tlv), big)

    def test_overlong_claim_rejected(self):
        with self.assertRaises(NdefError):
            unwrap_tlv(bytes([0x03, 0x40]) + b"\x00" * 4)


class TestCardUrlFits(unittest.TestCase):
    """The real payload has to fit an NTAG213's 144 usable bytes."""

    def test_fits_ntag213(self):
        payload = wrap_tlv(encode_message([uri_record(CARD_URL)]))
        self.assertLessEqual(len(payload), 144)

    def test_fits_with_ref_segment(self):
        payload = wrap_tlv(encode_message([uri_record(CARD_URL + "/nfc")]))
        self.assertLessEqual(len(payload), 144)


if __name__ == "__main__":
    unittest.main()


class TestNdefTlvOffset(unittest.TestCase):
    """Where the NDEF TLV belongs, given what a factory format left behind."""

    # Read off a real hecere NTAG213 silicone wristband, pages 4-11:
    #   01 03 A0 0C 34 | 03 00 | FE | 00 00 ...
    #   lock-control   | NDEF  | terminator
    REAL_NTAG213 = bytes.fromhex("0103A00C3403 00FE".replace(" ", "")) + b"\x00" * 8

    def test_skips_real_wristband_lock_control_tlv(self):
        from wristband.ndef import ndef_tlv_offset

        self.assertEqual(ndef_tlv_offset(self.REAL_NTAG213), 5)

    def test_blank_tag_starts_at_zero(self):
        from wristband.ndef import ndef_tlv_offset

        self.assertEqual(ndef_tlv_offset(b"\x00" * 144), 0)

    def test_ndef_tlv_first_means_zero(self):
        from wristband.ndef import ndef_tlv_offset

        self.assertEqual(ndef_tlv_offset(wrap_tlv(encode_message([uri_record(CARD_URL)]))), 0)

    def test_skips_memory_control_tlv(self):
        from wristband.ndef import ndef_tlv_offset

        self.assertEqual(ndef_tlv_offset(bytes([0x02, 0x03, 1, 2, 3, 0x03, 0x00, 0xFE])), 5)

    def test_skips_stacked_tlvs(self):
        from wristband.ndef import ndef_tlv_offset

        data = bytes([0x01, 0x03, 0xA0, 0x0C, 0x34, 0x02, 0x03, 1, 2, 3, 0x03, 0x00])
        self.assertEqual(ndef_tlv_offset(data), 10)

    def test_real_wristband_round_trips_through_unwrap(self):
        # The existing tag holds an empty NDEF TLV; unwrapping gives b"".
        self.assertEqual(unwrap_tlv(self.REAL_NTAG213), b"")

    def test_payload_still_fits_after_the_lock_control_tlv(self):
        offset = 5
        payload = wrap_tlv(encode_message([uri_record(CARD_URL + "/nfc")]))
        self.assertLessEqual(offset + len(payload), 144)
