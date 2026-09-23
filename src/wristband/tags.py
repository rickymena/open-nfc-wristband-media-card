"""Identifying NFC Forum Type 2 tags and knowing where it is safe to write.

A Type 2 tag is a flat array of 4-byte pages. The first four pages are
reserved (UID, internal byte, static lock bits, capability container); user
data starts at page 4. Writing past the end of user memory lands on the
dynamic lock bits and configuration pages, which can brick a tag permanently
-- so every bound in here is deliberately conservative.
"""

from __future__ import annotations

from dataclasses import dataclass

PAGE_SIZE = 4
FIRST_USER_PAGE = 4
CC_PAGE = 3
CC_MAGIC = 0xE1  # Marks the tag as NDEF-formatted.

# PC/SC Workgroup registered application provider identifier. In a storage-card
# ATR it is followed by the standard byte and a two-byte card name.
PCSC_RID = bytes([0xA0, 0x00, 0x00, 0x03, 0x06])

# PC/SC part 3 card names we care about.
_CARD_NAMES = {
    0x0001: "MIFARE Classic 1K",
    0x0002: "MIFARE Classic 4K",
    0x0003: "MIFARE Ultralight",  # NTAG21x also reports as this
    0x0026: "MIFARE Mini",
    0x003A: "MIFARE Ultralight C",
    0x0030: "Topaz/Jewel",
    0x003B: "FeliCa",
}

# NTAG21x and Ultralight all announce themselves as "MIFARE Ultralight" over
# PC/SC, so the capability container size byte is what actually tells them
# apart. Key is CC byte 2; value is (product, usable NDEF bytes).
_CC_SIZE_TO_PRODUCT = {
    0x06: ("MIFARE Ultralight / NTAG203", 48),
    0x12: ("NTAG213", 144),
    0x3E: ("NTAG215", 504),
    0x6D: ("NTAG216", 888),
}


@dataclass
class TagInfo:
    """What we managed to learn about the tag currently on the reader."""

    atr: bytes
    uid: bytes
    product: str
    cc: bytes | None = None
    capacity: int = 0
    formatted: bool = False
    writable: bool = True

    @property
    def last_user_page(self) -> int:
        """Highest page index it is safe to write.

        Derived from the capability container rather than from the product
        name, so an unrecognised tag that reports a sane CC still gets correct
        bounds instead of a guess.
        """
        return FIRST_USER_PAGE + (self.capacity // PAGE_SIZE) - 1

    def describe(self) -> str:
        uid = self.uid.hex(":").upper() if self.uid else "unknown"
        lines = [
            f"Product   : {self.product}",
            f"UID       : {uid}",
            f"ATR       : {self.atr.hex(' ').upper()}",
        ]
        if self.cc:
            lines.append(f"CC        : {self.cc.hex(' ').upper()}")
        if self.formatted:
            lines.append(
                f"Capacity  : {self.capacity} bytes NDEF "
                f"(pages {FIRST_USER_PAGE}-{self.last_user_page})"
            )
            lines.append(f"Access    : {'read/write' if self.writable else 'READ-ONLY (locked)'}")
        else:
            lines.append("Capacity  : unknown -- tag is not NDEF-formatted")
        return "\n".join(lines)


def card_name_from_atr(atr: bytes) -> str:
    """Pull the PC/SC storage-card name out of an ATR, if it carries one."""
    idx = atr.find(PCSC_RID)
    if idx == -1:
        return "unknown (non-storage ATR)"
    # RID, then one standard byte, then the two-byte card name.
    name_at = idx + len(PCSC_RID) + 1
    if name_at + 2 > len(atr):
        return "unknown (truncated ATR)"
    code = int.from_bytes(atr[name_at : name_at + 2], "big")
    return _CARD_NAMES.get(code, f"unknown (card name 0x{code:04X})")


def parse_cc(cc: bytes) -> tuple[str, int, bool, bool]:
    """Interpret a Type 2 capability container.

    Returns (product, capacity, formatted, writable). The four CC bytes are
    magic number, version, size/8, and access conditions.
    """
    if len(cc) < 4 or cc[0] != CC_MAGIC:
        return ("unformatted", 0, False, True)
    product, capacity = _CC_SIZE_TO_PRODUCT.get(
        cc[2], (f"unknown Type 2 tag (CC size 0x{cc[2]:02X})", cc[2] * 8)
    )
    # Low nibble of the access byte is the write condition; 0 means writable.
    writable = (cc[3] & 0x0F) == 0
    return (product, capacity, True, writable)


def identify(atr: bytes, uid: bytes, cc: bytes | None) -> TagInfo:
    """Combine ATR and capability container into a single picture of the tag."""
    info = TagInfo(atr=atr, uid=uid, product=card_name_from_atr(atr))
    if cc is None:
        return info
    product, capacity, formatted, writable = parse_cc(cc)
    info.cc = cc
    info.capacity = capacity
    info.formatted = formatted
    info.writable = writable
    if formatted:
        info.product = product
    return info


def blank_cc(capacity: int = 144) -> bytes:
    """A capability container for an unformatted tag. Defaults to NTAG213."""
    return bytes([CC_MAGIC, 0x10, capacity // 8, 0x00])


# -- Locking -----------------------------------------------------------
#
# All of the following is taken from the NXP NTAG213/215/216 data sheet,
# rev 3.2 (2 June 2015), sections 8.5.2 and 8.5.3, figures 9-12. Every write
# in here is bit-wise OR'ed into the existing value and is IRREVERSIBLE -- a
# bit set to 1 can never go back to 0 -- so the masks are stated explicitly
# rather than computed, and the block-locking bits are deliberately left
# alone (they freeze the lock configuration itself, which buys nothing once
# every lock bit is already set).

# Static lock bytes: page 2, bytes 2 and 3. They cover page 3 (the CC) through
# page 15 only.
#   byte 2: bit0 BL-CC | bit1 BL9-4 | bit2 BL15-10 | bit3 L-CC | bits4-7 L4..L7
#   byte 3: bits0-7 = L8..L15
# 0xF8 sets L-CC and L4..L7 while leaving the three block-locking bits clear.
STATIC_LOCK_PAGE = 2
STATIC_LOCK_MASK = (0xF8, 0xFF)

# Dynamic lock bytes cover page 16 upwards, and live on a different page per
# chip. Value is (page, byte0 mask, byte1 mask); byte 2 is block-locking bits
# and byte 3 is RFUI, both left at zero.
#
#   NTAG213 @ 0x28 - 2 pages per bit. byte0 bits0-7 = pages 16-17 .. 30-31,
#                    byte1 bits0-3 = pages 32-33 .. 38-39, bits4-7 RFUI.
#   NTAG215 @ 0x82 - 16 pages per bit. byte0 bits0-7 = pages 16-31 .. 128-129,
#                    byte1 entirely RFUI.
#   NTAG216 @ 0xE2 - 16 pages per bit. byte0 bits0-7 = pages 16-31 .. 128-143,
#                    byte1 bits0-5 = pages 144-159 .. 224-225, bits6-7 RFUI.
DYNAMIC_LOCK = {
    "NTAG213": (0x28, 0xFF, 0x0F),
    "NTAG215": (0x82, 0xFF, 0x00),
    "NTAG216": (0xE2, 0xFF, 0x3F),
}

# CC byte 3 access conditions: high nibble is read access, low nibble write.
# 0x0F leaves reads free and forbids writes.
CC_READ_ONLY = 0x0F
