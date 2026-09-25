"""Building and writing NDEF payloads.

Shared by the CLI and the web UI so the write path -- TLV preservation,
capacity checks, page bounds, read-back verification -- exists exactly once.
Nothing here prints; callers decide how to report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ndef import (
    NdefError,
    decode_message,
    encode_message,
    ndef_tlv_offset,
    smart_poster_record,
    text_record,
    unwrap_tlv,
    uri_record,
    wrap_tlv,
)
from .tags import (
    CC_PAGE,
    DYNAMIC_LOCK,
    FIRST_USER_PAGE,
    PAGE_SIZE,
    STATIC_LOCK_MASK,
    STATIC_LOCK_PAGE,
    blank_cc,
)


@dataclass
class WriteResult:
    ok: bool
    message: str
    notes: list[str] = field(default_factory=list)
    payload_bytes: int = 0
    used: int = 0
    capacity: int = 0
    first_page: int = 0
    last_page: int = 0


def build_url_payload(
    url: str,
    title: str | None = None,
    lang: str = "en",
    icon: bytes | None = None,
    icon_mime: str = "image/png",
) -> bytes:
    """TLV-wrapped NDEF message for a URL.

    A plain URI record unless a title or icon forces a Smart Poster, because
    iOS background tag reading ignores Smart Posters entirely.
    """
    if title or icon:
        record = smart_poster_record(url, title, lang, icon, icon_mime)
    else:
        record = uri_record(url)
    return wrap_tlv(encode_message([record]))


def build_text_payload(text: str, lang: str = "en") -> bytes:
    return wrap_tlv(encode_message([text_record(text, lang)]))


def read_records(rd, info) -> list[str]:
    """Human-readable NDEF records on the tag, or [] if there are none."""
    if not info.formatted:
        return []
    try:
        data = rd.read_pages(FIRST_USER_PAGE, info.capacity // PAGE_SIZE)
        return [str(r) for r in decode_message(unwrap_tlv(data))]
    except (NdefError, Exception):
        return []


def hard_locked(rd, info) -> bool:
    """True if any of the lock bits that `wristband lock` sets are set."""
    static = rd.read_pages(STATIC_LOCK_PAGE, 1)
    if static[2] & STATIC_LOCK_MASK[0] or static[3] & STATIC_LOCK_MASK[1]:
        return True
    entry = DYNAMIC_LOCK.get(info.product)
    if entry is None:
        return True  # unknown lock layout, so do not guess
    page, mask0, mask1 = entry
    dynamic = rd.read_pages(page, 1)
    return bool(dynamic[0] & mask0 or dynamic[1] & mask1)


def write_ndef(rd, info, payload: bytes, *, force: bool = False) -> WriteResult:
    """Write a TLV-wrapped payload into user memory and verify by reading back."""
    notes: list[str] = []

    if not info.formatted:
        if not force:
            return WriteResult(
                False,
                "Tag is not NDEF-formatted. Format it first, or retry with force.",
            )
        rd.write_page(CC_PAGE, blank_cc(144))
        info = rd.identify_tag()
        notes.append("Wrote a capability container (NTAG213 layout).")

    if not info.writable:
        # The CC flag only stops phones. Unless the lock bits are set too,
        # the data pages still accept writes from a PC/SC reader.
        if not force:
            return WriteResult(
                False, "Tag is soft-locked. Retry with force to rewrite it."
            )
        if hard_locked(rd, info):
            return WriteResult(
                False, "Tag is hard-locked; its contents can no longer be changed."
            )
        notes.append("Tag is soft-locked. Phones still cannot write it.")

    total_pages = info.capacity // PAGE_SIZE
    existing = rd.read_pages(FIRST_USER_PAGE, total_pages)

    # Preserve lock-control / memory-control TLVs the factory put ahead of the
    # NDEF TLV; they describe this tag's own memory layout.
    offset = ndef_tlv_offset(existing)
    if offset:
        notes.append(f"Preserved {offset} bytes of existing TLVs.")

    if offset + len(payload) > info.capacity:
        return WriteResult(
            False,
            f"Payload is {len(payload)} bytes and the NDEF area starts at byte "
            f"{offset}, but this tag holds {info.capacity}. Shorten the URL or "
            "use an NTAG215/216.",
        )

    page_index = offset // PAGE_SIZE
    head = existing[page_index * PAGE_SIZE : offset]
    buf = head + payload
    buf += b"\x00" * (-len(buf) % PAGE_SIZE)

    first = FIRST_USER_PAGE + page_index
    pages = len(buf) // PAGE_SIZE
    last = first + pages - 1
    if last > info.last_user_page:
        return WriteResult(
            False, "Refusing to write past user memory (would hit config pages)."
        )

    for i in range(pages):
        rd.write_page(first + i, buf[i * PAGE_SIZE : (i + 1) * PAGE_SIZE])

    # Read back rather than trusting status words: a tag pulled out of the
    # field mid-write can acknowledge pages that never landed.
    check = rd.read_pages(first, pages)
    if check[: len(head) + len(payload)] != head + payload:
        return WriteResult(
            False, "Verification failed -- data read back does not match. Try again."
        )

    used = offset + len(payload)
    return WriteResult(
        True,
        f"Verified. {used}/{info.capacity} bytes used.",
        notes=notes,
        payload_bytes=len(payload),
        used=used,
        capacity=info.capacity,
        first_page=first,
        last_page=last,
    )


def soft_lock(rd, info) -> WriteResult:
    """Set the NDEF read-only flag. Irreversible: the CC is one-time programmable."""
    if not info.formatted:
        return WriteResult(False, "Refusing to lock an unformatted tag.")
    if not info.writable:
        return WriteResult(True, "Tag is already marked read-only.")
    from .tags import CC_READ_ONLY

    cc = bytearray(info.cc)
    cc[3] |= CC_READ_ONLY
    rd.write_page(CC_PAGE, bytes(cc))
    after = rd.read_cc()
    if after is None or (after[3] & 0x0F) != 0x0F:
        return WriteResult(False, "Lock did not take -- the CC still reads writable.")
    return WriteResult(True, "Soft locked. Phones will refuse to write this tag.")


def soft_unlock(rd, info) -> WriteResult:
    """Try to clear the NDEF read-only flag.

    On genuine NXP silicon this cannot succeed. The capability container is
    one-time programmable: a write is OR'ed into the existing value, so
    clearing a bit is impossible and 0x0F stays 0x0F. The write still returns
    90 00, which is exactly why this verifies by reading the byte back instead
    of trusting the status word.

    Clone chips frequently do not implement OTP and will accept the change, so
    the attempt is worth making -- it just has to report honestly which
    happened.
    """
    from .tags import CC_READ_ONLY

    if not info.formatted:
        return WriteResult(False, "Tag is not NDEF-formatted; nothing to unlock.")
    if info.writable:
        return WriteResult(True, "Tag is already writable; nothing to do.")

    cc = bytearray(info.cc)
    cc[3] &= ~CC_READ_ONLY & 0xFF          # clear the write-access nibble
    rd.write_page(CC_PAGE, bytes(cc))

    after = rd.read_cc()
    if after is None:
        return WriteResult(False, "Could not read the capability container back.")
    if (after[3] & 0x0F) == 0:
        return WriteResult(
            True,
            f"Unlocked. CC is now {after.hex(' ').upper()} -- phones will write it again.",
            notes=["This chip does not enforce one-time-programmable CC bits, "
                   "which genuine NXP silicon does."],
        )
    return WriteResult(
        False,
        f"Could not unlock: CC still reads {after.hex(' ').upper()}.",
        notes=[
            "The capability container is one-time programmable, so the access "
            "byte can never be cleared once set.",
            "The tag's data is still rewritable with this reader -- only "
            "phones honour the read-only flag.",
        ],
    )
