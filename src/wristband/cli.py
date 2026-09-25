"""Command line interface for encoding NFC wristbands."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

from . import __version__
from .ndef import ICON_MIME, NdefError, decode_message, ndef_tlv_offset, unwrap_tlv
from .reader import NfcReader, ReaderError, list_readers
from .writer import build_text_payload, build_url_payload, soft_unlock, write_ndef
from . import audit
from . import ntag
from .tags import (
    CC_PAGE,
    CC_READ_ONLY,
    DYNAMIC_LOCK,
    FIRST_USER_PAGE,
    PAGE_SIZE,
    STATIC_LOCK_MASK,
    STATIC_LOCK_PAGE,
    blank_cc,
)

ENV_URL = "WRISTBAND_URL"


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _open(args) -> NfcReader:
    reader = NfcReader(args.reader)
    return reader.wait_for_tag(timeout=args.timeout)


def _write_payload(rd: NfcReader, info, payload: bytes, *, force: bool) -> int:
    """Write and verify, reporting to stdout. The logic lives in writer.py."""
    result = write_ndef(rd, info, payload, force=force)
    for note in result.notes:
        print(f"  {note}")
    if not result.ok:
        return _fail(result.message)
    print(f"Writing {result.payload_bytes} bytes to pages "
          f"{result.first_page}-{result.last_page}...")
    print(result.message)
    return 0


def _write_to_tags(args, payload: bytes) -> int:
    """Write the same payload to one tag, or to a run of them.

    A pack of stickers is the normal case for `--count`: present a tag, it is
    written and verified, you swap in the next one. UIDs already written are
    skipped, so a tag left sitting in the field is not written twice.
    """
    count = getattr(args, "count", 1)
    reader = NfcReader(args.reader)
    seen: set[bytes] = set()
    done = 0
    failed = 0

    try:
        while count == 0 or done < count:
            reader.wait_for_tag(timeout=args.timeout)
            info = reader.identify_tag()
            uid = info.uid

            if uid and uid in seen:
                print("  same tag as last time -- swap in the next one.")
                reader.wait_for_removal(timeout=args.timeout)
                continue

            marker = f"[{done + 1}/{count}]" if count else f"[{done + 1}]"
            print(f"{marker} {info.product}  UID {uid.hex(':').upper() or 'unknown'}")
            try:
                rc = _write_payload(reader, info, payload, force=args.force)
            finally:
                reader.close()

            audit.record(
                getattr(args, "command", "write"),
                uid=uid.hex(":").upper(), product=info.product, reader=reader.name,
                ok=rc == 0, bytes=len(payload),
                **({"url": args.url} if getattr(args, "url", None) else {}),
            )
            if rc == 0:
                if uid:
                    seen.add(uid)
                done += 1
            else:
                failed += 1

            if count == 0 or done < count:
                print("  remove the tag and present the next one "
                      "(Ctrl-C to stop).\n")
                reader.wait_for_removal(timeout=args.timeout)
    except KeyboardInterrupt:
        print()
    finally:
        reader.close()

    if count != 1:
        print(f"\nDone: {done} tag(s) written"
              + (f", {failed} failed" if failed else "") + ".")
    return 1 if failed else 0


def _write_empty_ndef(rd: NfcReader, total_pages: int) -> int:
    """Put an empty NDEF TLV + terminator where the NDEF area starts.

    Returns the page it was written to. Phones report a tag with no NDEF TLV
    at all as corrupt, so formatting leaves this marker behind.
    """
    existing = rd.read_pages(FIRST_USER_PAGE, total_pages)
    offset = ndef_tlv_offset(existing)
    page_index = offset // PAGE_SIZE
    head = existing[page_index * PAGE_SIZE : offset]
    buf = head + bytes([0x03, 0x00, 0xFE])
    buf += b"\x00" * (-len(buf) % PAGE_SIZE)
    page = FIRST_USER_PAGE + page_index
    for i in range(len(buf) // PAGE_SIZE):
        rd.write_page(page + i, buf[i * PAGE_SIZE : (i + 1) * PAGE_SIZE])
    return page + len(buf) // PAGE_SIZE - 1


def cmd_readers(args) -> int:
    found = list_readers()
    if not found:
        return _fail("no PC/SC readers found. Is pcscd running?")
    print("Readers:")
    for i, name in enumerate(found):
        print(f"  [{i}] {name}")
    return 0


def cmd_detect(args) -> int:
    with _open(args) as rd:
        print(f"Reader    : {rd.name}")
        info = rd.identify_tag()
        print(info.describe())
        if info.formatted:
            try:
                data = rd.read_pages(FIRST_USER_PAGE, info.capacity // PAGE_SIZE)
                records = decode_message(unwrap_tlv(data))
                print(f"Content   : {len(records)} record(s)")
                for r in records:
                    print(f"  {r}")
            except NdefError as e:
                print(f"Content   : none ({e})")
    return 0


def cmd_read(args) -> int:
    with _open(args) as rd:
        info = rd.identify_tag()
        if not info.formatted:
            return _fail("tag is not NDEF-formatted; nothing to read.")
        data = rd.read_pages(FIRST_USER_PAGE, info.capacity // PAGE_SIZE)
        try:
            records = decode_message(unwrap_tlv(data))
        except NdefError as e:
            return _fail(str(e))
        for r in records:
            print(r)
    return 0


def cmd_dump(args) -> int:
    with _open(args) as rd:
        info = rd.identify_tag()
        pages = (info.capacity // PAGE_SIZE) if info.formatted else 16
        data = rd.read_pages(0, pages + FIRST_USER_PAGE)
        for p in range(len(data) // PAGE_SIZE):
            chunk = data[p * PAGE_SIZE : (p + 1) * PAGE_SIZE]
            ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            note = {0: "UID", 1: "UID", 2: "lock", 3: "CC"}.get(p, "")
            print(f"  page {p:3d}  {chunk.hex(' ').upper()}  |{ascii_}|  {note}")
    return 0


def cmd_write_url(args) -> int:
    url = args.url or os.environ.get(ENV_URL)
    if not url:
        return _fail(f"no URL given. Pass one, or set ${ENV_URL}.")
    if args.ref:
        url = url.rstrip("/") + "/" + args.ref.lstrip("/")
    if "://" not in url and not url.startswith(("tel:", "mailto:")):
        return _fail(f"{url!r} has no scheme -- did you mean https://{url}?")
    if any(c.isspace() for c in url):
        return _fail(f"{url!r} contains spaces -- use %20 or a hyphen instead.")

    icon = None
    icon_mime = "image/png"
    if args.icon:
        path = pathlib.Path(args.icon)
        if not path.is_file():
            return _fail(f"icon file not found: {path}")
        suffix = path.suffix.lower()
        if suffix not in ICON_MIME:
            return _fail(
                f"unsupported icon type {suffix!r}; expected one of "
                + ", ".join(sorted(ICON_MIME))
            )
        icon_mime = ICON_MIME[suffix]
        icon = path.read_bytes()

    # A title or icon means the link has to be wrapped in a Smart Poster; a
    # bare URI record has nowhere to put them. That wrapper costs iPhone
    # compatibility outright, so it is never the default and it warns.
    if args.title or icon:
        print(
            "WARNING: a title or icon requires a Smart Poster record, and iOS\n"
            "         background tag reading ignores Smart Posters entirely --\n"
            "         an iPhone will show no notification when tapped.\n"
            "         Android is fine. See docs/COMPATIBILITY.md.",
            file=sys.stderr,
        )
    payload = build_url_payload(url, args.title, args.lang, icon, icon_mime)

    print(f"URL       : {url}")
    if args.title:
        print(f"Title     : {args.title}")
    if icon:
        print(f"Icon      : {args.icon} ({icon_mime}, {len(icon)} bytes)")
    return _write_to_tags(args, payload)


def cmd_write_text(args) -> int:
    payload = build_text_payload(args.text, args.lang)
    return _write_to_tags(args, payload)


def cmd_format(args) -> int:
    with _open(args) as rd:
        info = rd.identify_tag()
        if info.formatted and not args.force:
            return _fail(
                f"tag is already formatted ({info.product}, {info.capacity} bytes). "
                "Pass --force to rewrite the capability container."
            )
        print(f"Writing capability container for {args.capacity} bytes...")
        rd.write_page(CC_PAGE, blank_cc(args.capacity))
        # An empty NDEF TLV keeps phones from reporting the tag as corrupt.
        _write_empty_ndef(rd, args.capacity // PAGE_SIZE)
        print(rd.identify_tag().describe())
    return 0


def cmd_erase(args) -> int:
    with _open(args) as rd:
        info = rd.identify_tag()
        if not info.formatted:
            return _fail("tag is not formatted; nothing to erase.")
        pages = info.capacity // PAGE_SIZE
        print(f"Erasing {info.capacity} bytes...")
        start = _write_empty_ndef(rd, pages)
        for p in range(start + 1, FIRST_USER_PAGE + pages):
            rd.write_page(p, b"\x00\x00\x00\x00")
        print("Erased.")
    return 0


def cmd_lock(args) -> int:
    """Make the tag read-only.

    Two tiers, because they defend against different things:

    --soft  sets only the NDEF read-only flag in the capability container.
            Every phone NFC app honours it, which covers the realistic threat
            (a passer-by with a phone), and it leaves the tag recoverable with
            a reader like this one.
    default also sets the hardware lock bits. Nothing can ever write the tag
            again, including you, including a factory reader.

    Both are irreversible in the sense that lock bits only ever go 0 -> 1.
    """
    with _open(args) as rd:
        info = rd.identify_tag()
        if not info.formatted:
            return _fail("refusing to lock an unformatted tag -- write it first.")
        if not info.writable:
            print("Tag is already read-only.")
            return 0

        uid = info.uid.hex(":").upper() or "unknown"
        scope = "NDEF read-only flag" if args.soft else "NDEF flag AND hardware lock bits"
        if not args.yes:
            return _fail(
                f"this would set the {scope} on {info.product} (UID {uid}).\n"
                "       It is PERMANENT -- lock bits cannot be cleared, by anyone, ever.\n"
                "       Verify the tag first with 'wristband read', then re-run with --yes."
            )

        print(f"Locking {info.product} (UID {uid})...")

        # The CC is written first: the static lock bits include L-CC, and once
        # that is set page 3 can never be updated again.
        cc = bytearray(info.cc)
        cc[3] |= CC_READ_ONLY
        rd.write_page(CC_PAGE, bytes(cc))
        print("  CC access byte -> read-only")

        if args.soft:
            print("Soft-locked. Phone NFC apps will refuse to write this tag.")
            audit.record("lock", uid=uid, product=info.product, reader=rd.name,
                         ok=True, scope="soft (CC read-only flag)")
            return 0

        # Dynamic lock bits cover page 16 upwards and live on a chip-specific
        # page. A tag whose user memory ends at page 15 has none.
        entry = DYNAMIC_LOCK.get(info.product)
        if entry:
            page, b0, b1 = entry
            rd.write_page(page, bytes([b0, b1, 0x00, 0x00]))
            print(f"  dynamic lock bits (page {page}) -> pages 16-{info.last_user_page}")
        elif info.last_user_page > 15:
            print(
                f"  WARNING: {info.product} has user memory above page 15 but no known\n"
                f"           dynamic lock layout. Pages 16-{info.last_user_page} stay writable."
            )

        # Static lock bits last: they freeze pages 3-15, including the CC.
        current = rd.read_pages(STATIC_LOCK_PAGE, 1)
        # Bytes 0 and 1 of page 2 are unaffected by a write (datasheet 8.5.2),
        # so what we send for them does not matter.
        rd.write_page(
            STATIC_LOCK_PAGE,
            bytes([current[0], current[1], STATIC_LOCK_MASK[0], STATIC_LOCK_MASK[1]]),
        )
        print("  static lock bits -> pages 3-15")
        print("\nLocked. This tag is now permanently read-only.")
    return 0


def cmd_serve(args) -> int:
    from .web.server import serve

    return serve(args.host, args.port, args.reader)



def _config_for(rd, info):
    """Read the configuration pages for whatever chip is present."""
    page = ntag.CONFIG_PAGE.get(info.product)
    if page is None:
        return None, None, (
            f"{info.product} has no NTAG21x configuration pages "
            "(or is an unrecognised chip)."
        )
    raw = rd.read_config(page)
    return ntag.parse_config(raw), (page, raw), None


def _apply_config(rd, info, cfg, page, raw, *, accept_lockout: bool) -> int:
    """Write configuration pages after checking the change is recoverable."""
    danger = ntag.protection_locks_out(cfg, info.last_user_page, rd.can_authenticate())
    if danger and not accept_lockout:
        print(f"\nREFUSING: {danger}", file=sys.stderr)
        print("\nPass --accept-lockout if that is genuinely what you want.",
              file=sys.stderr)
        return 1
    if danger:
        print(f"\nWARNING: {danger}\nProceeding because --accept-lockout was given.\n",
              file=sys.stderr)

    p41, p42 = ntag.build_config_pages(cfg, raw)
    rd.write_page(page, p41)
    rd.write_page(page + 1, p42)

    back = ntag.parse_config(rd.read_config(page))
    print("\n".join("  " + line for line in ntag.describe(back, info.last_user_page)))
    audit.record("config", uid=info.uid.hex(":").upper(), product=info.product,
                 reader=rd.name, ok=True, mirror=back.mirroring,
                 counter=back.nfc_cnt_en, auth0=back.auth0, cfglck=back.cfglck)
    return 0


def cmd_capabilities(args) -> int:
    with _open(args) as rd:
        info = rd.identify_tag()
        print(f"Reader    : {rd.name}")
        print(f"Tag       : {info.product}  UID {info.uid.hex(':').upper()}")
        genuine = bool(info.uid) and info.uid[0] == 0x04
        print(f"Silicon   : {'genuine NXP' if genuine else 'compatible clone (UID does not start with 04)'}")
        print()
        support = rd.native_command_support()
        ok = support["transparent_session"]
        print(f"Native commands (PWD_AUTH, GET_VERSION, READ_CNT, READ_SIG):")
        print(f"  transparent session (FF C2) : {'yes' if ok else 'no'}")
        if support["detail"]:
            print(f"  {support['detail']}")
        print()
        if ok:
            print("  This reader can authenticate, so password protection is usable.")
        else:
            print("  This reader CANNOT authenticate. Password protection can be")
            print("  written but never cleared -- 'password set' will refuse without")
            print("  --accept-lockout. Everything else works: NDEF, locking,")
            print("  ASCII mirroring and the NFC counter are all plain memory writes.")
    return 0


def cmd_config(args) -> int:
    with _open(args) as rd:
        info = rd.identify_tag()
        cfg, ctx, err = _config_for(rd, info)
        if err:
            return _fail(err)
        print(f"Tag       : {info.product}  UID {info.uid.hex(':').upper()}")
        print(f"Config    : pages {ctx[0]}-{ctx[0] + 3}")
        print("\n".join("  " + line for line in ntag.describe(cfg, info.last_user_page)))
        print(f"  Strong mod   : {'on' if cfg.strong_modulation else 'off'}")
        print(f"  Raw          : {ctx[1].hex(' ').upper()}")
    return 0


def cmd_mirror(args) -> int:
    modes = {v: k for k, v in ntag.MIRROR_NAMES.items()}
    with _open(args) as rd:
        info = rd.identify_tag()
        cfg, ctx, err = _config_for(rd, info)
        if err:
            return _fail(err)
        page, raw = ctx
        cfg.mirror_conf = modes[args.mode]
        if args.mode == "off":
            cfg.mirror_page = 0x00
        else:
            cfg.mirror_page = args.page
            cfg.mirror_byte = args.byte
            if args.page <= 3:
                return _fail("--page must be above 3; pages 0-3 are UID, lock bits and CC.")
            if not ntag.mirror_fits(args.page, args.byte, cfg.mirror_conf, info.last_user_page):
                width = ntag.MIRROR_WIDTH[cfg.mirror_conf]
                return _fail(
                    f"a {width}-character mirror at page {args.page} byte {args.byte} "
                    f"runs past user memory (last page {info.last_user_page})."
                )
            print(f"Mirroring {args.mode} at page {args.page}, byte {args.byte} "
                  f"({ntag.MIRROR_WIDTH[cfg.mirror_conf]} hex characters).")
            print("The tag substitutes this into the data it returns, so write a URL")
            print("with a placeholder of exactly that many characters at that offset.")
        rc = _apply_config(rd, info, cfg, page, raw, accept_lockout=args.accept_lockout)
        if rc == 0 and args.mode != "off":
            _verify_mirror(rd, info, cfg)
        return rc


def _verify_mirror(rd, info, cfg) -> None:
    """Check the chip actually performs the mirror it was told to.

    The configuration bytes are ordinary memory, so they store on any chip.
    Whether the silicon acts on them is another matter -- compatible clones
    routinely accept the configuration and then ignore it. Read the target
    region back and see whether the UID actually appears there.
    """
    if cfg.mirror_conf not in (ntag.MIRROR_UID, ntag.MIRROR_BOTH) or not info.uid:
        return
    start = cfg.mirror_page * PAGE_SIZE + cfg.mirror_byte
    first_page = start // PAGE_SIZE
    try:
        data = rd.read_pages(first_page, 4)
    except ReaderError:
        return
    window = data[start - first_page * PAGE_SIZE :][:14]
    expected = info.uid.hex().upper().encode()[:14]
    if window == expected:
        print("\n  Verified: the tag is substituting its UID into the data it returns.")
    else:
        print("\n  WARNING: the mirror is configured but the tag is NOT applying it.")
        print(f"           Expected {expected.decode()} at page {cfg.mirror_page}, "
              f"byte {cfg.mirror_byte};")
        print(f"           the tag still returns {window.decode('ascii', 'replace')!r}.")
        genuine = info.uid[0] == 0x04
        if not genuine:
            print("           This is a compatible clone (UID does not start with 04).")
            print("           Clones commonly store the configuration and ignore it.")
        else:
            print("           Check the placeholder is exactly 14 characters at that offset.")


def cmd_counter(args) -> int:
    with _open(args) as rd:
        info = rd.identify_tag()
        cfg, ctx, err = _config_for(rd, info)
        if err:
            return _fail(err)
        page, raw = ctx
        cfg.nfc_cnt_en = args.state == "on"
        cfg.nfc_cnt_pwd_prot = bool(args.password_protect)
        print(f"NFC counter {'enabled' if cfg.nfc_cnt_en else 'disabled'}.")
        if cfg.nfc_cnt_en:
            print("It increments on the first READ after each power-on, and can be")
            print("mirrored into the NDEF data with 'wristband mirror counter'.")
        return _apply_config(rd, info, cfg, page, raw, accept_lockout=args.accept_lockout)


def cmd_password(args) -> int:
    with _open(args) as rd:
        info = rd.identify_tag()
        cfg, ctx, err = _config_for(rd, info)
        if err:
            return _fail(err)
        page, raw = ctx

        if args.state == "status":
            active = cfg.protection_active(info.last_user_page)
            print(f"Password protection: {'ACTIVE' if active else 'inactive'}")
            print(f"  AUTH0        : 0x{cfg.auth0:02X}"
                  + (f" (protects pages {cfg.auth0}-{info.last_user_page})" if active else " (disabled)"))
            print(f"  Scope        : {'reads and writes' if cfg.prot else 'writes only'}")
            print(f"  Attempt limit: {cfg.authlim or 'unlimited'}")
            print(f"  Config lock  : {'LOCKED' if cfg.cfglck else 'open'}")
            print("\nPWD and PACK read back as zero by design -- they are write-only.")
            print(f"This reader can authenticate: {'yes' if rd.can_authenticate() else 'NO'}")
            return 0

        if args.state == "disable":
            cfg.auth0 = ntag.AUTH0_DISABLED
            cfg.prot = False
            print("Disabling password protection (AUTH0 = 0xFF).")
            print("Note: this only works if the tag is not already protected --")
            print("a protected tag needs authentication before its config accepts writes.")
            return _apply_config(rd, info, cfg, page, raw, accept_lockout=args.accept_lockout)

        # set
        try:
            pwd = bytes.fromhex(args.password.replace(":", ""))
            pack = bytes.fromhex(args.pack.replace(":", ""))
        except ValueError:
            return _fail("--password and --pack must be hex, e.g. --password DEADBEEF")
        if len(pwd) != 4:
            return _fail(f"password must be exactly 4 bytes (8 hex chars), got {len(pwd)}")
        if len(pack) != 2:
            return _fail(f"pack must be exactly 2 bytes (4 hex chars), got {len(pack)}")
        if not (0 <= args.auth0 <= 0xFF):
            return _fail("--auth0 must be 0-255")

        cfg.auth0 = args.auth0
        cfg.prot = bool(args.protect_reads)
        danger = ntag.protection_locks_out(cfg, info.last_user_page, rd.can_authenticate())
        if danger and not args.accept_lockout:
            print(f"REFUSING: {danger}", file=sys.stderr)
            print("\nThe password would be set but never usable from this reader.",
                  file=sys.stderr)
            print("Set it with a phone app (NFC Tools) instead, or pass --accept-lockout.",
                  file=sys.stderr)
            return 1

        # PWD and PACK are written before AUTH0 takes effect, so order matters.
        rd.write_page(page + 2, pwd)
        rd.write_page(page + 3, pack + b"\x00\x00")
        print(f"PWD and PACK written. Protection from page {args.auth0}, "
              f"{'reads and writes' if cfg.prot else 'writes only'}.")
        print("Write the password down: it cannot be read back.")
        return _apply_config(rd, info, cfg, page, raw, accept_lockout=args.accept_lockout)


def cmd_unlock(args) -> int:
    with _open(args) as rd:
        info = rd.identify_tag()
        print(f"Tag       : {info.product}  UID {info.uid.hex(':').upper()}")
        result = soft_unlock(rd, info)
        for note in result.notes:
            print(f"  {note}")
        audit.record("unlock", uid=info.uid.hex(":").upper(), product=info.product,
                     reader=rd.name, ok=result.ok, message=result.message)
        if not result.ok:
            return _fail(result.message)
        print(result.message)
    return 0


def cmd_history(args) -> int:
    path = pathlib.Path(args.file).expanduser() if args.file else audit.default_path()
    entries = audit.read(path, limit=args.limit, uid=args.uid or "", action=args.action or "")
    if not entries:
        print(f"No entries in {path}")
        return 0
    if args.json:
        for entry in entries:
            print(json.dumps(entry))
    else:
        print(f"{path}  ({len(entries)} entries)\n")
        for entry in entries:
            print("  " + audit.summarise(entry))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wristband",
        description="Encode NFC wristbands and tags with a URL, using any PC/SC reader.",
    )
    p.add_argument("--version", action="version", version=f"wristband {__version__}")
    p.add_argument("-r", "--reader", help="substring of the reader name to use")
    p.add_argument(
        "-t", "--timeout", type=float, default=30.0,
        help="seconds to wait for a tag (default: 30)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("readers", help="list connected PC/SC readers").set_defaults(func=cmd_readers)
    sub.add_parser("detect", help="identify the tag and show its contents").set_defaults(func=cmd_detect)
    sub.add_parser("read", help="read the NDEF records on the tag").set_defaults(func=cmd_read)
    sub.add_parser("dump", help="hex dump every page").set_defaults(func=cmd_dump)

    w = sub.add_parser("write-url", help="write a URL to the tag")
    w.add_argument("url", nargs="?", help=f"the URL (default: ${ENV_URL})")
    w.add_argument("--ref", help="append a path segment, e.g. --ref nfc, for click attribution")
    w.add_argument(
        "--title",
        help="label for Android and NFC apps. Wraps the link in a Smart Poster, "
             "which iPhones silently ignore -- see docs/COMPATIBILITY.md",
    )
    w.add_argument("--lang", default="en", help="language of --title (default: en)")
    w.add_argument(
        "--icon",
        help="image to embed in the Smart Poster. Needs an NTAG215/216, and "
             "breaks iPhone detection -- see docs/COMPATIBILITY.md",
    )
    w.add_argument(
        "--force", action="store_true",
        help="format the tag first if needed, or rewrite a soft-locked tag",
    )
    w.add_argument(
        "--count", type=int, default=1, metavar="N",
        help="write N tags in a row, swapping each one out; 0 means keep "
             "going until Ctrl-C. Handy for a pack of stickers.",
    )
    w.set_defaults(func=cmd_write_url)

    t = sub.add_parser("write-text", help="write a plain text record")
    t.add_argument("text")
    t.add_argument("--lang", default="en")
    t.add_argument("--force", action="store_true",
                   help="format the tag first if needed, or rewrite a soft-locked tag")
    t.add_argument("--count", type=int, default=1, metavar="N",
                   help="write N tags in a row; 0 means until Ctrl-C")
    t.set_defaults(func=cmd_write_text)

    f = sub.add_parser("format", help="write an NDEF capability container")
    f.add_argument("--capacity", type=int, default=144, help="usable bytes (NTAG213=144)")
    f.add_argument("--force", action="store_true", help="rewrite an existing container")
    f.set_defaults(func=cmd_format)

    sub.add_parser("erase", help="blank the NDEF data area").set_defaults(func=cmd_erase)

    sub.add_parser("capabilities",
                   help="what this reader can and cannot do with this tag"
                   ).set_defaults(func=cmd_capabilities)
    sub.add_parser("config", help="show the NTAG21x configuration pages"
                   ).set_defaults(func=cmd_config)

    mi = sub.add_parser("mirror", help="configure the ASCII mirror (UID / NFC counter)")
    mi.add_argument("mode", choices=["off", "uid", "counter", "both"])
    mi.add_argument("--page", type=int, default=4, help="page where the mirror starts")
    mi.add_argument("--byte", type=int, default=0, choices=[0, 1, 2, 3],
                    help="byte offset within that page")
    mi.add_argument("--accept-lockout", action="store_true", help=argparse.SUPPRESS)
    mi.set_defaults(func=cmd_mirror)

    ct = sub.add_parser("counter", help="enable or disable the NFC read counter")
    ct.add_argument("state", choices=["on", "off"])
    ct.add_argument("--password-protect", action="store_true",
                    help="only return the counter after authentication")
    ct.add_argument("--accept-lockout", action="store_true", help=argparse.SUPPRESS)
    ct.set_defaults(func=cmd_counter)

    pw = sub.add_parser("password", help="32-bit password protection (see docs/PASSWORD.md)")
    pw.add_argument("state", choices=["status", "set", "disable"])
    pw.add_argument("--password", default="", metavar="HEX", help="4 bytes, e.g. DEADBEEF")
    pw.add_argument("--pack", default="0000", metavar="HEX", help="2-byte acknowledge")
    pw.add_argument("--auth0", type=int, default=0xFF, metavar="PAGE",
                    help="first protected page; 0xFF disables protection")
    pw.add_argument("--protect-reads", action="store_true",
                    help="protect reads as well as writes")
    pw.add_argument("--accept-lockout", action="store_true",
                    help="proceed even though this reader cannot authenticate")
    pw.set_defaults(func=cmd_password)

    sv = sub.add_parser("serve", help="run the web UI")
    sv.add_argument("--host", default="0.0.0.0", help="bind address (default: 0.0.0.0)")
    sv.add_argument("--port", type=int, default=8080, help="port (default: 8080)")
    sv.set_defaults(func=cmd_serve)

    sub.add_parser("unlock",
                   help="try to clear the NDEF read-only flag (impossible on genuine NXP)"
                   ).set_defaults(func=cmd_unlock)

    hi = sub.add_parser("history", help="show the audit log of writes and locks")
    hi.add_argument("--limit", type=int, default=20, help="most recent N entries (0 = all)")
    hi.add_argument("--uid", help="only this tag")
    hi.add_argument("--action", help="only this action, e.g. write-url")
    hi.add_argument("--json", action="store_true", help="raw JSON Lines")
    hi.add_argument("--file", help="read a different log file")
    hi.set_defaults(func=cmd_history)

    lk = sub.add_parser("lock", help="make the tag read-only (cannot be undone)")
    lk.add_argument("--yes", action="store_true", help="confirm this irreversible action")
    lk.add_argument(
        "--soft", action="store_true",
        help="set only the NDEF read-only flag, not the hardware lock bits",
    )
    lk.set_defaults(func=cmd_lock)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ReaderError, NdefError) as e:
        return _fail(str(e))
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
