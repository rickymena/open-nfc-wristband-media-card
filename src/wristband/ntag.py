"""NTAG21x configuration: ASCII mirroring, the NFC counter, and passwords.

Pages 41-44 on an NTAG213 (131-134 on a 215, 227-230 on a 216) are ordinary
memory. They can be read with READ BINARY and written with UPDATE BINARY, so
every configurable feature of the chip is reachable through a plain PC/SC
reader -- with one exception noted below.

Layouts are from the NXP NTAG213/215/216 data sheet rev 3.2, section 8.5.7,
tables 8-11. Writes here change how the tag behaves permanently in some
cases, so the bit positions are spelled out rather than inferred.

THE EXCEPTION: setting a password is not the same as being able to use one.
Authenticating requires the native PWD_AUTH command (0x1B), which has no
PC/SC storage-card mapping. A reader that cannot send it can still *enable*
protection -- and will then be permanently unable to write the tag again.
`protection_locks_out()` exists so callers can refuse that.
"""

from __future__ import annotations

from dataclasses import dataclass

# First configuration page per product.
CONFIG_PAGE = {"NTAG213": 0x29, "NTAG215": 0x83, "NTAG216": 0xE3}

# AUTH0 values at or above this disable protection on any NTAG21x, because no
# such page exists on the chip.
AUTH0_DISABLED = 0xFF

# MIRROR_PAGE values of 0x03 or below disable mirroring (page 3 is the CC).
MIRROR_PAGE_DISABLED = 0x00

MIRROR_NONE, MIRROR_UID, MIRROR_COUNTER, MIRROR_BOTH = 0, 1, 2, 3
MIRROR_NAMES = {
    MIRROR_NONE: "off",
    MIRROR_UID: "uid",
    MIRROR_COUNTER: "counter",
    MIRROR_BOTH: "both",
}

# An ASCII mirror is written as hex characters: 14 for a 7-byte UID, 6 for the
# 3-byte counter, and 1 separator when both are mirrored.
MIRROR_WIDTH = {MIRROR_NONE: 0, MIRROR_UID: 14, MIRROR_COUNTER: 6, MIRROR_BOTH: 21}


@dataclass
class Config:
    """The four configuration pages, decoded."""

    mirror_conf: int = MIRROR_NONE
    mirror_byte: int = 0
    strong_modulation: bool = False
    mirror_page: int = 0
    auth0: int = AUTH0_DISABLED
    prot: bool = False          # True: password protects reads as well as writes
    cfglck: bool = False        # True: configuration permanently frozen
    nfc_cnt_en: bool = False
    nfc_cnt_pwd_prot: bool = False
    authlim: int = 0            # 0 = unlimited attempts

    @property
    def mirroring(self) -> str:
        return MIRROR_NAMES.get(self.mirror_conf, "?")

    def mirror_enabled(self) -> bool:
        # Mirroring needs both a mode and a target page above the CC.
        return self.mirror_conf != MIRROR_NONE and self.mirror_page > 0x03

    def protection_active(self, last_user_page: int) -> bool:
        """True if AUTH0 points at a page that actually exists."""
        return self.auth0 <= last_user_page


def parse_config(pages: bytes) -> Config:
    """Decode 16 bytes read from the first configuration page onward."""
    if len(pages) < 16:
        raise ValueError(f"need 16 bytes of configuration pages, got {len(pages)}")
    mirror, _rfui, mirror_page, auth0 = pages[0], pages[1], pages[2], pages[3]
    access = pages[4]
    return Config(
        mirror_conf=(mirror >> 6) & 0x03,
        mirror_byte=(mirror >> 4) & 0x03,
        strong_modulation=bool(mirror & 0x04),
        mirror_page=mirror_page,
        auth0=auth0,
        prot=bool(access & 0x80),
        cfglck=bool(access & 0x40),
        nfc_cnt_en=bool(access & 0x10),
        nfc_cnt_pwd_prot=bool(access & 0x08),
        authlim=access & 0x07,
    )


def build_mirror_byte(cfg: Config) -> int:
    """MIRROR byte: bits 7-6 mode, 5-4 byte offset, 2 strong modulation."""
    return (
        ((cfg.mirror_conf & 0x03) << 6)
        | ((cfg.mirror_byte & 0x03) << 4)
        | (0x04 if cfg.strong_modulation else 0x00)
    )


def build_access_byte(cfg: Config) -> int:
    """ACCESS byte: PROT, CFGLCK, NFC_CNT_EN, NFC_CNT_PWD_PROT, AUTHLIM."""
    return (
        (0x80 if cfg.prot else 0)
        | (0x40 if cfg.cfglck else 0)
        | (0x10 if cfg.nfc_cnt_en else 0)
        | (0x08 if cfg.nfc_cnt_pwd_prot else 0)
        | (cfg.authlim & 0x07)
    )


def build_config_pages(cfg: Config, current: bytes = b"") -> tuple[bytes, bytes]:
    """The two writable configuration pages.

    The data sheet says to write RFUI bits as zero, but real tags ship with a
    non-zero value in one of them (byte 1 of the ACCESS page reads 0x05 from
    the factory). When the current contents are supplied those bytes are left
    exactly as they are, so a configuration change never silently alters
    undocumented state.
    """
    rfui_41 = current[1] if len(current) > 1 else 0x00
    rfui_42 = current[5:8] if len(current) >= 8 else b"\x00\x00\x00"
    page0 = bytes([build_mirror_byte(cfg), rfui_41, cfg.mirror_page & 0xFF, cfg.auth0 & 0xFF])
    page1 = bytes([build_access_byte(cfg)]) + bytes(rfui_42)
    return page0, page1


def protection_locks_out(cfg: Config, last_user_page: int, can_authenticate: bool) -> str | None:
    """Explain why enabling this configuration would be unrecoverable, or None.

    Returns a message when the change would leave the tag permanently
    unwritable by this setup, so the caller can refuse rather than discover it
    afterwards.
    """
    if cfg.protection_active(last_user_page) and not can_authenticate:
        return (
            f"AUTH0 is 0x{cfg.auth0:02X}, which protects pages "
            f"{cfg.auth0}-{last_user_page}, but this reader cannot send the "
            "native PWD_AUTH command. Once written, nothing here could "
            "authenticate and the tag could never be written again."
        )
    if cfg.cfglck:
        return (
            "CFGLCK permanently freezes the configuration pages. There is no "
            "way to clear it, on any reader."
        )
    if cfg.authlim:
        return (
            f"AUTHLIM is {cfg.authlim}: after {cfg.authlim} failed "
            "authentications the tag refuses all further attempts, forever, "
            "and the protected pages become permanently read-only."
        )
    return None


def mirror_fits(mirror_page: int, mirror_byte: int, conf: int, last_user_page: int) -> bool:
    """Whether an ASCII mirror starting at this position fits in user memory."""
    if conf == MIRROR_NONE:
        return True
    start = (mirror_page * 4) + mirror_byte
    end = start + MIRROR_WIDTH[conf]
    return end <= (last_user_page + 1) * 4


def describe(cfg: Config, last_user_page: int) -> list[str]:
    """Human-readable summary lines."""
    out = []
    if cfg.mirror_enabled():
        out.append(
            f"ASCII mirror : {cfg.mirroring} at page {cfg.mirror_page} "
            f"byte {cfg.mirror_byte} ({MIRROR_WIDTH[cfg.mirror_conf]} chars)"
        )
    else:
        out.append("ASCII mirror : off")
    out.append(f"NFC counter  : {'enabled' if cfg.nfc_cnt_en else 'disabled'}"
               + (", password protected" if cfg.nfc_cnt_pwd_prot else ""))
    if cfg.protection_active(last_user_page):
        scope = "reads and writes" if cfg.prot else "writes"
        out.append(f"Password     : ACTIVE from page {cfg.auth0}, protects {scope}")
    else:
        out.append(f"Password     : inactive (AUTH0=0x{cfg.auth0:02X})")
    out.append(f"Attempt limit: {cfg.authlim or 'unlimited'}")
    out.append(f"Config lock  : {'LOCKED permanently' if cfg.cfglck else 'open'}")
    return out
