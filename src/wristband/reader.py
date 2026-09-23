"""Talking to a PC/SC contactless reader.

Everything here goes through the PC/SC storage-card APDU mapping, which is
what lets the same code drive an ACR122U, an Alcor Link AK9567, an Identiv or
a phone-sized CCID dongle without per-vendor escapes.
"""

from __future__ import annotations

import time

from .apdu import (
    SW_OK,
    WRITE_PADDED,
    WRITE_PLAIN,
    explain as _explain,
    get_uid as _build_get_uid,
    read_binary,
    update_binary_padded,
    update_binary_plain,
)
from .tags import PAGE_SIZE, CC_PAGE, TagInfo, identify

try:
    from smartcard.System import readers as _pcsc_readers
    from smartcard.Exceptions import (
        CardConnectionException,
        NoCardException,
        SmartcardException,
    )
except ImportError as exc:  # pragma: no cover - environment specific
    raise SystemExit(
        "pyscard is not installed.\n"
        "  Fedora : sudo dnf install python3-pyscard\n"
        "  Debian : sudo apt install python3-pyscard\n"
        "  pip    : pip install pyscard  (needs swig + pcsc-lite development headers)"
    ) from exc

# Reader names that indicate the contactless slot on a dual-interface device.
_CONTACTLESS_HINTS = ("contactless", "picc", "cl ", "-cl", "nfc", "rfid")


class ReaderError(RuntimeError):
    """Something went wrong between us and the reader or the tag."""


def list_readers() -> list[str]:
    return [str(r) for r in _pcsc_readers()]


def _looks_contactless(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _CONTACTLESS_HINTS)


class NfcReader:
    """A connection to one tag sitting on one reader.

    Used as a context manager so the card connection is always released, which
    matters with pcscd -- a leaked connection keeps the slot busy.
    """

    def __init__(self, name: str | None = None) -> None:
        available = _pcsc_readers()
        if not available:
            raise ReaderError(
                "No PC/SC readers found. Is the reader plugged in and pcscd running?\n"
                "  sudo systemctl enable --now pcscd.socket"
            )
        if name:
            matches = [r for r in available if name.lower() in str(r).lower()]
            if not matches:
                names = "\n  ".join(str(r) for r in available)
                raise ReaderError(f"No reader matching {name!r}. Available:\n  {names}")
            self.reader = matches[0]
        else:
            # Prefer a slot that advertises itself as contactless; a dual-interface
            # reader exposes a contact slot too, and that one will never see a tag.
            contactless = [r for r in available if _looks_contactless(str(r))]
            self.reader = contactless[0] if contactless else available[0]
        self.connection = None
        self.write_mode: str | None = None

    @property
    def name(self) -> str:
        return str(self.reader)

    def __enter__(self) -> NfcReader:
        self.connection = self.reader.createConnection()
        self.connection.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        if self.connection is not None:
            try:
                self.connection.disconnect()
            except SmartcardException:
                pass
            self.connection = None

    def wait_for_tag(self, timeout: float = 30.0, poll: float = 0.25) -> NfcReader:
        """Block until a tag is present, then connect. Call instead of __enter__."""
        self.connection = self.reader.createConnection()
        deadline = time.monotonic() + timeout
        announced = False
        while True:
            try:
                self.connection.connect()
                return self
            except (NoCardException, CardConnectionException):
                if not announced:
                    print(f"Hold the tag against the reader ({self.name})...", flush=True)
                    announced = True
                if time.monotonic() >= deadline:
                    raise ReaderError(f"No tag detected within {timeout:.0f}s.") from None
                time.sleep(poll)

    def close(self) -> None:
        """Release the card connection but keep the reader for the next tag."""
        if self.connection is not None:
            try:
                self.connection.disconnect()
            except SmartcardException:
                pass
            self.connection = None

    def wait_for_removal(self, timeout: float = 120.0, poll: float = 0.25) -> None:
        """Block until the tag is lifted off the reader.

        Used between tags when writing a batch, so the same sticker is not
        written twice just because it is still sitting in the field.
        """
        self.close()
        deadline = time.monotonic() + timeout
        while True:
            probe = self.reader.createConnection()
            try:
                probe.connect()
                probe.disconnect()
            except (NoCardException, CardConnectionException):
                return
            except SmartcardException:
                return
            if time.monotonic() >= deadline:
                raise ReaderError(f"Tag still present after {timeout:.0f}s.")
            time.sleep(poll)

    # -- raw APDU layer -------------------------------------------------

    def transmit(self, apdu: list[int]) -> tuple[bytes, int, int]:
        if self.connection is None:
            raise ReaderError("Not connected -- use NfcReader as a context manager.")
        data, sw1, sw2 = self.connection.transmit(apdu)
        return bytes(data), sw1, sw2

    def _checked(self, apdu: list[int], what: str) -> bytes:
        data, sw1, sw2 = self.transmit(apdu)
        if (sw1, sw2) != SW_OK:
            raise ReaderError(f"{what} failed (SW={sw1:02X}{sw2:02X}) {_explain(sw1, sw2)}")
        return data

    @property
    def atr(self) -> bytes:
        if self.connection is None:
            raise ReaderError("Not connected.")
        return bytes(self.connection.getATR())

    # -- Type 2 tag operations ------------------------------------------

    def get_uid(self) -> bytes:
        return self._checked(_build_get_uid(), "Get UID")

    def read_pages(self, start: int, count: int) -> bytes:
        """Read `count` pages from `start`.

        A Type 2 READ always returns 16 bytes (4 pages) and wraps around at the
        end of memory, so reads are issued in 4-page chunks and trimmed.
        """
        out = bytearray()
        page = start
        remaining = count
        while remaining > 0:
            chunk = min(4, remaining)
            data = self._checked(
                read_binary(page, chunk * PAGE_SIZE), f"Read page {page}"
            )
            out += data[: chunk * PAGE_SIZE]
            page += chunk
            remaining -= chunk
        return bytes(out)

    def write_page(self, page: int, data: bytes) -> None:
        """Write one 4-byte page, in whichever APDU form this reader accepts."""
        if len(data) != PAGE_SIZE:
            raise ValueError(f"a page is exactly {PAGE_SIZE} bytes, got {len(data)}")

        if self.write_mode != WRITE_PADDED:
            _, sw1, sw2 = self.transmit(update_binary_plain(page, data))
            if (sw1, sw2) == SW_OK:
                self.write_mode = WRITE_PLAIN
                return
            if self.write_mode == WRITE_PLAIN:
                # This form worked before, so the failure is real.
                raise ReaderError(
                    f"Write page {page} failed (SW={sw1:02X}{sw2:02X}) {_explain(sw1, sw2)}"
                )

        # Pad out of the tag's own current contents rather than with zeros, so
        # that a reader which does honour all 16 bytes rewrites the following
        # three pages with what they already hold instead of blanking them.
        try:
            context = self.read_pages(page, 4)
        except ReaderError:
            context = b""

        _, sw1, sw2 = self.transmit(update_binary_padded(page, data, context))
        if (sw1, sw2) != SW_OK:
            raise ReaderError(
                f"Write page {page} failed (SW={sw1:02X}{sw2:02X}) {_explain(sw1, sw2)}"
            )
        self.write_mode = WRITE_PADDED

    def native_command_support(self) -> dict:
        """Can this reader send native NTAG commands such as PWD_AUTH?

        Three ways exist in principle, and a reader may support none of them.
        Without one, a password can be *set* on a tag but never *used*, which
        is the difference between protecting a tag and bricking it.
        """
        result = {"transparent_session": False, "ccid_escape": False, "detail": ""}

        # PC/SC v2 part 3 transparent session: the only standard route.
        try:
            _, sw1, sw2 = self.transmit([0xFF, 0xC2, 0x00, 0x00, 0x02, 0x81, 0x00])
            if (sw1, sw2) == SW_OK:
                result["transparent_session"] = True
                self.transmit([0xFF, 0xC2, 0x00, 0x00, 0x02, 0x82, 0x00])
            else:
                result["detail"] = f"transparent session refused (SW={sw1:02X}{sw2:02X})"
        except Exception as exc:
            result["detail"] = f"transparent session unavailable: {exc}"

        return result

    def can_authenticate(self) -> bool:
        """True only if a native PWD_AUTH could actually be sent."""
        return self.native_command_support()["transparent_session"]

    def read_config(self, page: int) -> bytes:
        """The four configuration pages, starting at `page`."""
        return self.read_pages(page, 4)

    def read_cc(self) -> bytes | None:
        try:
            return self.read_pages(CC_PAGE, 1)
        except ReaderError:
            return None

    def identify_tag(self) -> TagInfo:
        uid = b""
        try:
            uid = self.get_uid()
        except ReaderError:
            pass
        return identify(self.atr, uid, self.read_cc())
