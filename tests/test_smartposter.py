"""Smart Poster tests -- the record type that gives a tag a title and icon."""

import unittest

from wristband.ndef import (
    ACTION_OPEN,
    ICON_MIME,
    NdefError,
    RTD_SMART_POSTER,
    TNF_MIME,
    TNF_WELL_KNOWN,
    decode_message,
    encode_message,
    parse_smart_poster,
    smart_poster_record,
    uri_record,
    wrap_tlv,
)

CARD_URL = "https://example.com/c/3b9c2f47-5d18-4e6a-9f02-7c81ad5e6b34"
# A real NTAG213 wristband loses 5 bytes to its factory lock-control TLV.
NTAG213_BUDGET = 144 - 5
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001"
)


def on_tag(record) -> int:
    return len(wrap_tlv(encode_message([record])))


class TestStructure(unittest.TestCase):
    def test_is_a_well_known_sp_record(self):
        rec = smart_poster_record(CARD_URL, "Alex Rivera")
        self.assertEqual(rec.tnf, TNF_WELL_KNOWN)
        self.assertEqual(rec.type, RTD_SMART_POSTER)

    def test_payload_is_a_nested_ndef_message(self):
        rec = smart_poster_record(CARD_URL, "Alex Rivera")
        inner = decode_message(rec.payload)
        self.assertEqual(len(inner), 2)

    def test_uri_record_comes_first(self):
        # A naive parser that takes the first record must still get the link,
        # not the label.
        inner = decode_message(smart_poster_record(CARD_URL, "Title").payload)
        self.assertEqual(inner[0].type, b"U")

    def test_nested_message_has_mb_and_me_set(self):
        inner_bytes = smart_poster_record(CARD_URL, "Title").payload
        self.assertTrue(inner_bytes[0] & 0x80, "first inner record needs MB")
        recs = decode_message(inner_bytes)
        self.assertEqual(len(recs), 2)


class TestRoundTrip(unittest.TestCase):
    def test_uri_only(self):
        sp = parse_smart_poster(smart_poster_record(CARD_URL))
        self.assertEqual(sp["uri"], CARD_URL)
        self.assertIsNone(sp["title"])

    def test_title_round_trips(self):
        sp = parse_smart_poster(smart_poster_record(CARD_URL, "Alex Rivera"))
        self.assertEqual(sp["uri"], CARD_URL)
        self.assertEqual(sp["title"], "Alex Rivera")
        self.assertEqual(sp["lang"], "en")

    def test_non_ascii_title(self):
        sp = parse_smart_poster(smart_poster_record(CARD_URL, "Café crème", lang="es"))
        self.assertEqual(sp["title"], "Café crème")
        self.assertEqual(sp["lang"], "es")

    def test_action_round_trips(self):
        sp = parse_smart_poster(smart_poster_record(CARD_URL, action=ACTION_OPEN))
        self.assertEqual(sp["action"], ACTION_OPEN)

    def test_icon_round_trips(self):
        rec = smart_poster_record(CARD_URL, "T", icon=PNG_1PX, icon_mime="image/png")
        sp = parse_smart_poster(rec)
        self.assertEqual(sp["icon"], ("image/png", len(PNG_1PX)))

    def test_icon_is_a_mime_record(self):
        rec = smart_poster_record(CARD_URL, icon=PNG_1PX, icon_mime="image/png")
        mime = [r for r in decode_message(rec.payload) if r.tnf == TNF_MIME]
        self.assertEqual(len(mime), 1)
        self.assertEqual(mime[0].type, b"image/png")

    def test_rejects_non_smart_poster(self):
        with self.assertRaises(NdefError):
            parse_smart_poster(uri_record(CARD_URL))

    def test_str_shows_title_and_uri(self):
        text = str(smart_poster_record(CARD_URL, "Alex Rivera"))
        self.assertIn("SMART POSTER", text)
        self.assertIn(CARD_URL, text)
        self.assertIn("Alex Rivera", text)


class TestCapacity(unittest.TestCase):
    """What actually fits on the wristband."""

    def test_bare_uri_fits(self):
        self.assertLessEqual(on_tag(uri_record(CARD_URL)), NTAG213_BUDGET)

    def test_title_fits_on_ntag213(self):
        for title in ("Alex Rivera", "Alex Rivera - Example Co"):
            with self.subTest(title=title):
                self.assertLessEqual(
                    on_tag(smart_poster_record(CARD_URL, title)), NTAG213_BUDGET
                )

    def test_title_costs_about_seven_bytes_plus_its_text(self):
        base = on_tag(uri_record(CARD_URL))
        titled = on_tag(smart_poster_record(CARD_URL, "Alex Rivera"))
        # Smart Poster framing plus the text record, not a per-character tax.
        self.assertLess(titled - base - len("Alex Rivera"), 16)

    def test_icon_does_not_fit_on_ntag213(self):
        # Documented limitation, asserted so it cannot silently start passing.
        rec = smart_poster_record(CARD_URL, "Alex Rivera", icon=PNG_1PX)
        self.assertGreater(on_tag(rec), NTAG213_BUDGET)

    def test_icon_fits_on_ntag216(self):
        rec = smart_poster_record(CARD_URL, "Alex Rivera", icon=b"\x89PNG" + bytes(500))
        self.assertLessEqual(on_tag(rec), 888)

    def test_long_payload_uses_long_record_form(self):
        rec = smart_poster_record(CARD_URL, "T", icon=bytes(600))
        self.assertGreater(len(rec.payload), 255)
        self.assertEqual(parse_smart_poster(rec)["uri"], CARD_URL)


class TestIconMime(unittest.TestCase):
    def test_common_extensions_map(self):
        self.assertEqual(ICON_MIME[".png"], "image/png")
        self.assertEqual(ICON_MIME[".jpg"], "image/jpeg")
        self.assertEqual(ICON_MIME[".jpeg"], "image/jpeg")


class TestDefaultStaysCompatible(unittest.TestCase):
    """A plain URL must never silently become a Smart Poster.

    iOS background tag reading ignores Smart Poster records outright, so the
    wrapper has to stay opt-in. Verified on hardware: an iPhone raised no
    notification for the Smart Poster version of this exact tag, and did for
    the bare URI record.
    """

    def test_uri_record_is_not_a_smart_poster(self):
        rec = uri_record(CARD_URL)
        self.assertEqual(rec.type, b"U")
        self.assertNotEqual(rec.type, RTD_SMART_POSTER)

    def test_bare_uri_is_the_smaller_encoding(self):
        self.assertLess(
            on_tag(uri_record(CARD_URL)),
            on_tag(smart_poster_record(CARD_URL, "Alex Rivera")),
        )

    def test_ios_readable_layout_has_uri_as_first_record(self):
        # iOS processes the first URL record in the message; it must be at the
        # top level, not nested inside a Smart Poster.
        msg = decode_message(encode_message([uri_record(CARD_URL)]))
        self.assertEqual(msg[0].type, b"U")
