"""NDEF encoding and decoding, with no third-party dependencies.

This implements the slice of the NFC Forum NDEF 1.0 spec that a "tap to open
my link" tag actually needs: well-known URI and Text records, short and long
form, no chunking. Keeping it dependency-free means the whole write path is
auditable in one file -- which matters when the thing you are encoding is a
capability URL.
"""

from __future__ import annotations

from dataclasses import dataclass

# Flags in an NDEF record header byte.
MB = 0x80  # Message Begin
ME = 0x40  # Message End
CF = 0x20  # Chunk Flag
SR = 0x10  # Short Record (1-byte payload length)
IL = 0x08  # ID Length present
TNF_MASK = 0x07

TNF_WELL_KNOWN = 0x01

RTD_URI = b"U"
RTD_TEXT = b"T"

# NFC Forum URI Record Type Definition, table 6. The index into this tuple is
# the identifier code stored as the first payload byte; the rest of the payload
# is the remainder of the URI. Order matters -- see _split_uri.
URI_PREFIXES: tuple[str, ...] = (
    "",  # 0x00 - no abbreviation
    "http://www.",
    "https://www.",
    "http://",
    "https://",
    "tel:",
    "mailto:",
    "ftp://anonymous:anonymous@",
    "ftp://ftp.",
    "ftps://",
    "sftp://",
    "smb://",
    "nfs://",
    "ftp://",
    "dav://",
    "news:",
    "telnet://",
    "imap:",
    "rtsp://",
    "urn:",
    "pop:",
    "sip:",
    "sips:",
    "tftp:",
    "btspp://",
    "btl2cap://",
    "btgoep://",
    "tcpobex://",
    "irdaobex://",
    "file://",
    "urn:epc:id:",
    "urn:epc:tag:",
    "urn:epc:pat:",
    "urn:epc:raw:",
    "urn:epc:",
    "urn:nfc:",
)

# Type 2 tag TLV block types (NFC Forum Type 2 Tag spec, section 2.3).
TLV_NULL = 0x00
TLV_LOCK_CONTROL = 0x01
TLV_MEMORY_CONTROL = 0x02
TLV_NDEF = 0x03
TLV_PROPRIETARY = 0xFD
TLV_TERMINATOR = 0xFE


class NdefError(ValueError):
    """Raised when data on a tag is not valid NDEF."""


@dataclass(frozen=True)
class Record:
    """One NDEF record."""

    tnf: int
    type: bytes
    payload: bytes

    def __str__(self) -> str:
        if self.tnf == TNF_WELL_KNOWN and self.type == RTD_URI:
            return f"URI   {decode_uri_payload(self.payload)}"
        if self.tnf == TNF_WELL_KNOWN and self.type == RTD_TEXT:
            text, lang = decode_text_payload(self.payload)
            return f"TEXT  [{lang}] {text}"
        if self.tnf == TNF_WELL_KNOWN and self.type == RTD_SMART_POSTER:
            sp = parse_smart_poster(self)
            bits = [f"SMART POSTER", f"  URI   {sp['uri']}"]
            if sp["title"] is not None:
                bits.append(f"  TITLE [{sp['lang']}] {sp['title']}")
            if sp["action"] is not None:
                bits.append(f"  ACTION {sp['action']}")
            if sp["icon"] is not None:
                bits.append(f"  ICON  {sp['icon'][0]}, {sp['icon'][1]} bytes")
            return "\n".join(bits)
        pretty = self.type.decode("ascii", "replace")
        return f"TNF{self.tnf} {pretty!r} {self.payload!r}"


def _split_uri(uri: str) -> tuple[int, str]:
    """Pick the URI abbreviation code that saves the most bytes.

    Longest match wins, so "https://www.example.com" uses 0x02 rather than the
    shorter-matching 0x04. Index 0 is skipped because it is the empty prefix.
    """
    best_code, best_len = 0, 0
    for code, prefix in enumerate(URI_PREFIXES[1:], start=1):
        if len(prefix) > best_len and uri.startswith(prefix):
            best_code, best_len = code, len(prefix)
    return best_code, uri[best_len:]


def encode_uri_payload(uri: str) -> bytes:
    code, rest = _split_uri(uri)
    return bytes([code]) + rest.encode("utf-8")


def decode_uri_payload(payload: bytes) -> str:
    if not payload:
        raise NdefError("empty URI payload")
    code = payload[0]
    if code >= len(URI_PREFIXES):
        raise NdefError(f"unknown URI prefix code 0x{code:02X}")
    return URI_PREFIXES[code] + payload[1:].decode("utf-8", "replace")


def encode_text_payload(text: str, lang: str = "en") -> bytes:
    lang_bytes = lang.encode("ascii")
    if len(lang_bytes) > 0x3F:
        raise NdefError("language code too long")
    # Status byte: bit 7 clear = UTF-8, low 6 bits = language code length.
    return bytes([len(lang_bytes)]) + lang_bytes + text.encode("utf-8")


def decode_text_payload(payload: bytes) -> tuple[str, str]:
    if not payload:
        raise NdefError("empty text payload")
    status = payload[0]
    lang_len = status & 0x3F
    encoding = "utf-16" if status & 0x80 else "utf-8"
    lang = payload[1 : 1 + lang_len].decode("ascii", "replace")
    return payload[1 + lang_len :].decode(encoding, "replace"), lang


def uri_record(uri: str) -> Record:
    return Record(TNF_WELL_KNOWN, RTD_URI, encode_uri_payload(uri))


def text_record(text: str, lang: str = "en") -> Record:
    return Record(TNF_WELL_KNOWN, RTD_TEXT, encode_text_payload(text, lang))


def encode_message(records: list[Record]) -> bytes:
    """Serialise records into an NDEF message."""
    if not records:
        raise NdefError("cannot encode an empty message")
    out = bytearray()
    for i, rec in enumerate(records):
        short = len(rec.payload) < 256
        header = rec.tnf & TNF_MASK
        if i == 0:
            header |= MB
        if i == len(records) - 1:
            header |= ME
        if short:
            header |= SR
        out.append(header)
        out.append(len(rec.type))
        if short:
            out.append(len(rec.payload))
        else:
            out += len(rec.payload).to_bytes(4, "big")
        out += rec.type
        out += rec.payload
    return bytes(out)


def decode_message(data: bytes) -> list[Record]:
    """Parse an NDEF message. Chunked records are rejected rather than guessed at."""
    records: list[Record] = []
    i = 0
    while i < len(data):
        header = data[i]
        i += 1
        if header & CF:
            raise NdefError("chunked records are not supported")
        if i >= len(data):
            raise NdefError("truncated record: missing type length")
        type_len = data[i]
        i += 1
        if header & SR:
            if i >= len(data):
                raise NdefError("truncated record: missing payload length")
            payload_len = data[i]
            i += 1
        else:
            if i + 4 > len(data):
                raise NdefError("truncated record: missing payload length")
            payload_len = int.from_bytes(data[i : i + 4], "big")
            i += 4
        id_len = 0
        if header & IL:
            if i >= len(data):
                raise NdefError("truncated record: missing ID length")
            id_len = data[i]
            i += 1
        end = i + type_len + id_len + payload_len
        if end > len(data):
            raise NdefError("truncated record: payload runs past end of message")
        rec_type = data[i : i + type_len]
        payload = data[i + type_len + id_len : end]
        records.append(Record(header & TNF_MASK, rec_type, payload))
        i = end
        if header & ME:
            break
    if not records:
        raise NdefError("no records found")
    return records


def wrap_tlv(message: bytes) -> bytes:
    """Wrap an NDEF message in a Type 2 tag NDEF TLV, with a terminator."""
    if len(message) < 0xFF:
        head = bytes([TLV_NDEF, len(message)])
    else:
        # Three-byte length form: 0xFF followed by a 16-bit big-endian length.
        head = bytes([TLV_NDEF, 0xFF]) + len(message).to_bytes(2, "big")
    return head + message + bytes([TLV_TERMINATOR])


def unwrap_tlv(data: bytes) -> bytes:
    """Find the NDEF TLV in a Type 2 tag data area and return its value.

    Skips null, lock-control, memory-control and proprietary TLVs, which sit
    ahead of the NDEF TLV on plenty of real tags.
    """
    i = 0
    while i < len(data):
        tag = data[i]
        if tag == TLV_NULL:
            i += 1
            continue
        if tag == TLV_TERMINATOR:
            break
        i += 1
        if i >= len(data):
            raise NdefError("truncated TLV: missing length")
        length = data[i]
        i += 1
        if length == 0xFF:
            if i + 2 > len(data):
                raise NdefError("truncated TLV: missing extended length")
            length = int.from_bytes(data[i : i + 2], "big")
            i += 2
        if tag == TLV_NDEF:
            if i + length > len(data):
                raise NdefError("NDEF TLV claims more data than the tag holds")
            return data[i : i + length]
        i += length
    raise NdefError("no NDEF TLV found -- the tag may be unformatted")


def ndef_tlv_offset(data: bytes) -> int:
    """Byte offset into a Type 2 data area where the NDEF TLV belongs.

    Factory-formatted tags often carry a lock-control or memory-control TLV
    ahead of the NDEF TLV -- an NTAG213 wristband typically starts with
    ``01 03 A0 0C 34``. Those TLVs describe the tag's own memory layout, so a
    writer that starts at byte 0 and stomps on them can leave the tag
    misconfigured for other readers. Walk past them instead.

    Returns 0 for a blank or unrecognised data area, which is the right place
    to start writing anyway.
    """
    skippable = (TLV_LOCK_CONTROL, TLV_MEMORY_CONTROL, TLV_PROPRIETARY)
    i = 0
    while i < len(data):
        tag = data[i]
        if tag == TLV_NULL:
            i += 1
            continue
        if tag not in skippable:
            # NDEF TLV, terminator, or something we do not understand: this is
            # where the NDEF TLV goes.
            return i
        i += 1
        if i >= len(data):
            return 0
        length = data[i]
        i += 1
        if length == 0xFF:
            if i + 2 > len(data):
                return 0
            length = int.from_bytes(data[i : i + 2], "big")
            i += 2
        i += length
    return 0


# Smart Poster (NFC Forum RTD-SmartPoster). A Smart Poster record's payload is
# itself an NDEF message: one mandatory URI record plus optional title, action
# and icon records. It is what lets a tag show a label instead of leaving
# the phone to fall back on "Website".
RTD_SMART_POSTER = b"Sp"
RTD_ACTION = b"act"

TNF_MIME = 0x02

# Action record values.
ACTION_OPEN = 0x00  # launch the URI (browser)
ACTION_SAVE = 0x01  # save for later
ACTION_EDIT = 0x02  # open for editing

ICON_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}


def smart_poster_record(
    uri: str,
    title: str | None = None,
    lang: str = "en",
    icon: bytes | None = None,
    icon_mime: str = "image/png",
    action: int | None = None,
) -> Record:
    """Build a Smart Poster wrapping `uri`.

    The URI record is written first. The spec does not fix record order, but a
    naive parser that simply takes the first record still finds the link that
    way, whereas a title-first layout would hand it the label instead.
    """
    inner: list[Record] = [uri_record(uri)]
    if title is not None:
        inner.append(text_record(title, lang))
    if action is not None:
        inner.append(Record(TNF_WELL_KNOWN, RTD_ACTION, bytes([action])))
    if icon is not None:
        inner.append(Record(TNF_MIME, icon_mime.encode("ascii"), icon))
    return Record(TNF_WELL_KNOWN, RTD_SMART_POSTER, encode_message(inner))


def parse_smart_poster(record: Record) -> dict:
    """Pull the URI, title, action and icon back out of a Smart Poster."""
    if record.tnf != TNF_WELL_KNOWN or record.type != RTD_SMART_POSTER:
        raise NdefError("not a Smart Poster record")
    out: dict = {"uri": None, "title": None, "lang": None, "action": None, "icon": None}
    for r in decode_message(record.payload):
        if r.tnf == TNF_WELL_KNOWN and r.type == RTD_URI:
            out["uri"] = decode_uri_payload(r.payload)
        elif r.tnf == TNF_WELL_KNOWN and r.type == RTD_TEXT:
            out["title"], out["lang"] = decode_text_payload(r.payload)
        elif r.tnf == TNF_WELL_KNOWN and r.type == RTD_ACTION and r.payload:
            out["action"] = r.payload[0]
        elif r.tnf == TNF_MIME and r.type.startswith(b"image/"):
            out["icon"] = (r.type.decode("ascii"), len(r.payload))
    if out["uri"] is None:
        raise NdefError("Smart Poster has no URI record")
    return out
