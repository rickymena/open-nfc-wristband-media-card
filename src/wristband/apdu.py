"""APDU construction for PC/SC contactless readers.

Split out from reader.py so the command encoding -- including the reader
quirk handling below -- can be tested without pyscard or hardware present.

All commands here are the PC/SC part 3 storage-card mapping, not vendor
escapes, which is what lets one code path drive readers from several vendors.
"""

from __future__ import annotations

PAGE_SIZE = 4
WRITE_BLOCK = 16

SW_OK = (0x90, 0x00)

# How a reader is willing to phrase a Type 2 page write.
#
#   plain  - FF D6 00 <page> 04 <4 bytes>, exactly as the storage-card mapping
#            specifies. ACR122U and most readers accept this.
#   padded - FF D6 00 <page> 10 <16 bytes>. The Alcor Link AK9567 (sold as
#            "Generic EMV Smartcard Reader", USB 2ce3:9567) rejects Lc=04 with
#            6300 and accepts only a 16-byte APDU. Measured behaviour: it
#            writes the first page and silently discards the other 12 bytes.
#
# Because that discard is not guaranteed across readers, the padded form is
# built from the tag's surrounding contents, so a reader that does write all
# four pages rewrites the neighbours with what they already hold.
WRITE_PLAIN = "plain"
WRITE_PADDED = "padded"


def get_uid() -> list[int]:
    return [0xFF, 0xCA, 0x00, 0x00, 0x00]


def read_binary(page: int, length: int) -> list[int]:
    return [0xFF, 0xB0, 0x00, page, length]


def update_binary_plain(page: int, data: bytes) -> list[int]:
    if len(data) != PAGE_SIZE:
        raise ValueError(f"a page is exactly {PAGE_SIZE} bytes, got {len(data)}")
    return [0xFF, 0xD6, 0x00, page, PAGE_SIZE] + list(data)


def update_binary_padded(page: int, data: bytes, context: bytes = b"") -> list[int]:
    """Build the 16-byte write form.

    `context` is what the tag currently holds starting at `page`; the new page
    is spliced over its first four bytes. Missing context is zero-filled,
    which is safe for the readers that discard it and no worse than a blank
    tag for any that do not.
    """
    if len(data) != PAGE_SIZE:
        raise ValueError(f"a page is exactly {PAGE_SIZE} bytes, got {len(data)}")
    block = bytearray(context[:WRITE_BLOCK])
    block += bytes(WRITE_BLOCK - len(block))
    block[:PAGE_SIZE] = data
    return [0xFF, 0xD6, 0x00, page, WRITE_BLOCK] + list(block)


def explain(sw1: int, sw2: int) -> str:
    """Turn the common status words into something actionable."""
    if (sw1, sw2) == (0x63, 0x00):
        return "-- the tag rejected it (locked page, tag moved, or the reader needs the 16-byte write form)"
    if (sw1, sw2) == (0x6A, 0x81):
        return "-- the reader does not support this operation on this tag type"
    if (sw1, sw2) == (0x6A, 0x82):
        return "-- address out of range for this tag"
    if sw1 == 0x6F:
        return "-- reader/tag communication error; try repositioning the tag"
    return ""
