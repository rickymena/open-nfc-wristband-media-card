"""A small web UI for the tag writer.

Standard library only -- http.server plus a background polling thread. There
is no framework here on purpose: the container stays tiny and the only
runtime dependency is still pyscard.

Live tag presence is pushed to the browser over Server-Sent Events, so the
page reacts the moment a tag lands on the reader instead of polling.
"""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import ntag
from ..ndef import ndef_tlv_offset
from ..reader import NfcReader, ReaderError, list_readers
from ..tags import FIRST_USER_PAGE, PAGE_SIZE

# Presence detection goes through SCardGetStatusChange, which reports whether a
# card is in the field WITHOUT connecting to it. That matters: pyscard's
# disconnect() issues SCardDisconnect(SCARD_UNPOWER_CARD), which powers the tag
# down. Polling by connect/disconnect therefore resets the tag several times a
# second and any concurrent write dies with "Card was reset" or a bare 6300.
try:
    from smartcard.scard import (
        SCARD_E_TIMEOUT,
        SCARD_S_SUCCESS,
        SCARD_SCOPE_USER,
        SCARD_STATE_CHANGED,
        SCARD_STATE_PRESENT,
        SCARD_STATE_UNAWARE,
        SCardEstablishContext,
        SCardGetStatusChange,
        SCardReleaseContext,
    )
    _HAVE_STATUS_CHANGE = True
except ImportError:  # pragma: no cover - very old pyscard
    _HAVE_STATUS_CHANGE = False
from .. import audit
from ..writer import (
    build_text_payload,
    build_url_payload,
    hard_locked,
    read_records,
    soft_lock,
    soft_unlock,
    write_ndef,
)

STATIC = Path(__file__).parent / "static"

# How long SCardGetStatusChange blocks before looping. This is a wait, not a
# poll: it returns immediately when a tag arrives or leaves, so a short value
# costs nothing and a long one only delays noticing a reader being unplugged.
STATUS_TIMEOUT_MS = 1000

# Fallback interval, used only when SCardGetStatusChange is unavailable.
POLL_INTERVAL = 1.0


class TagMonitor:
    """Polls the reader in the background and publishes what it sees.

    All reader access -- polling and writing alike -- goes through `io_lock`,
    because a card connection is exclusive and a poll landing mid-write would
    break both.
    """

    def __init__(self, reader_name: str | None = None) -> None:
        self.reader_name = reader_name
        self.io_lock = threading.RLock()
        self._cond = threading.Condition()
        self._state: dict = {"present": False, "reader": None, "error": None}
        self._version = 0
        self._stop = threading.Event()
        # Probing native-command support sends a real APDU, so do it once per
        # reader rather than on every 400 ms poll.
        self._can_auth: bool | None = None

    # -- state plumbing --------------------------------------------------

    def _publish(self, state: dict) -> None:
        with self._cond:
            if state == self._state:
                return
            self._state = state
            self._version += 1
            self._cond.notify_all()

    def snapshot(self) -> tuple[dict, int]:
        with self._cond:
            return dict(self._state), self._version

    def wait_for_change(self, since: int, timeout: float) -> tuple[dict, int]:
        with self._cond:
            self._cond.wait_for(lambda: self._version != since, timeout=timeout)
            return dict(self._state), self._version

    # -- reader access ---------------------------------------------------

    def _read_once(self) -> dict:
        try:
            names = list_readers()
        except Exception as exc:  # pragma: no cover - depends on pcscd
            return {"present": False, "reader": None, "error": f"PC/SC error: {exc}"}

        if not names:
            return {
                "present": False,
                "reader": None,
                "readers": [],
                "error": "No PC/SC reader found. Is pcscd running and the reader plugged in?",
            }

        rd = NfcReader(self.reader_name)
        base = {"reader": rd.name, "readers": names, "error": None}
        try:
            rd.__enter__()
        except Exception:
            # No tag in the field: the normal idle case, not an error.
            return {**base, "present": False}

        try:
            info = rd.identify_tag()
            uid = info.uid.hex(":").upper() if info.uid else ""
            records = read_records(rd, info)

            if self._can_auth is None:
                try:
                    self._can_auth = rd.can_authenticate()
                except Exception:
                    self._can_auth = False

            config = None
            cfg_page = ntag.CONFIG_PAGE.get(info.product)
            if cfg_page is not None:
                try:
                    cfg = ntag.parse_config(rd.read_config(cfg_page))
                    config = {
                        "mirror": cfg.mirroring,
                        "mirrorEnabled": cfg.mirror_enabled(),
                        "mirrorPage": cfg.mirror_page,
                        "mirrorByte": cfg.mirror_byte,
                        "counter": cfg.nfc_cnt_en,
                        "counterProtected": cfg.nfc_cnt_pwd_prot,
                        "auth0": cfg.auth0,
                        "protectionActive": cfg.protection_active(info.last_user_page),
                        "protectReads": cfg.prot,
                        "cfglck": cfg.cfglck,
                        "authlim": cfg.authlim,
                        "strongModulation": cfg.strong_modulation,
                        "configPage": cfg_page,
                    }
                except Exception:
                    config = None

            # Where the NDEF TLV starts, so the page can work out where a
            # mirror placeholder lands inside a URL.
            ndef_offset = 0
            if info.formatted:
                try:
                    area = rd.read_pages(FIRST_USER_PAGE, info.capacity // PAGE_SIZE)
                    ndef_offset = ndef_tlv_offset(area)
                except Exception:
                    pass

            # Soft-locked tags can still be rewritten from here; hard-locked
            # ones cannot. The page needs to know which.
            locked_hard = False
            if info.formatted and not info.writable:
                try:
                    locked_hard = hard_locked(rd, info)
                except Exception:
                    locked_hard = True

            return {
                **base,
                "present": True,
                "uid": uid,
                "config": config,
                "ndefOffset": ndef_offset,
                "canAuthenticate": bool(self._can_auth),
                "lastUserPage": info.last_user_page,
                # Byte 0 of a UID is the ISO 7816-6 manufacturer code; NXP is 04.
                "genuine": bool(info.uid) and info.uid[0] == 0x04,
                "product": info.product,
                "capacity": info.capacity,
                "formatted": info.formatted,
                "writable": info.writable,
                "hardLocked": locked_hard,
                "records": records,
            }
        except Exception as exc:
            return {**base, "present": True, "error": str(exc)}
        finally:
            rd.close()

    def _loop(self) -> None:
        if _HAVE_STATUS_CHANGE:
            try:
                self._watch()
                return
            except Exception as exc:  # pragma: no cover - falls back below
                self._publish({"present": False, "error": f"watch failed: {exc}"})
        self._poll_loop()

    def _watch(self) -> None:
        """Wait on card arrival/removal without ever powering the tag down."""
        hresult, ctx = SCardEstablishContext(SCARD_SCOPE_USER)
        if hresult != SCARD_S_SUCCESS:
            raise RuntimeError(f"SCardEstablishContext failed: {hresult:#x}")
        try:
            name = None
            states = None
            present = None
            while not self._stop.is_set():
                if name is None:
                    try:
                        name = NfcReader(self.reader_name).name
                        states = [(name, SCARD_STATE_UNAWARE)]
                        present = None
                    except ReaderError as exc:
                        self._publish({"present": False, "reader": None,
                                       "readers": [], "error": str(exc)})
                        self._stop.wait(2.0)
                        continue

                hresult, updated = SCardGetStatusChange(ctx, STATUS_TIMEOUT_MS, states)
                if hresult == SCARD_E_TIMEOUT:
                    continue
                if hresult != SCARD_S_SUCCESS:
                    name = None           # reader unplugged; re-resolve it
                    continue

                _, eventstate, _atr = updated[0]
                # Carry the observed state forward so the next call blocks
                # until something actually changes.
                states = [(name, eventstate & ~SCARD_STATE_CHANGED)]

                now = bool(eventstate & SCARD_STATE_PRESENT)
                if now == present:
                    continue
                present = now

                if not now:
                    self._publish({"present": False, "reader": name,
                                   "readers": list_readers(), "error": None})
                    continue

                # A tag just arrived. Connect once, read everything, release.
                with self.io_lock:
                    self._publish(self._read_once())
        finally:
            SCardReleaseContext(ctx)

    def _poll_loop(self) -> None:
        """Connect-based fallback. Disturbs the tag, so it polls slowly."""
        while not self._stop.is_set():
            try:
                with self.io_lock:
                    state = self._read_once()
                self._publish(state)
            except Exception as exc:  # pragma: no cover - defensive
                self._publish({"present": False, "error": str(exc)})
            self._stop.wait(POLL_INTERVAL)

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="tag-monitor").start()

    def stop(self) -> None:
        self._stop.set()

    # -- actions ---------------------------------------------------------

    def perform(self, action: str, body: dict) -> dict:
        """Run a write or lock against whatever tag is present."""
        with self.io_lock:
            rd = NfcReader(self.reader_name)
            try:
                rd.__enter__()
            except Exception:
                return {"ok": False, "message": "No tag on the reader."}
            try:
                info = rd.identify_tag()
                if action == "write-url":
                    url = (body.get("url") or "").strip()
                    if not url:
                        return {"ok": False, "message": "No URL given."}
                    if "://" not in url and not url.startswith(("tel:", "mailto:")):
                        return {"ok": False, "message": f"{url!r} has no scheme."}
                    ref = (body.get("ref") or "").strip()
                    if ref:
                        url = url.rstrip("/") + "/" + ref.lstrip("/")
                    if any(c.isspace() for c in url):
                        return {"ok": False, "message": f"{url!r} contains spaces."}
                    title = (body.get("title") or "").strip() or None
                    payload = build_url_payload(url, title)
                    result = write_ndef(rd, info, payload, force=bool(body.get("force")))
                    extra = {"url": url}
                elif action == "write-text":
                    text = body.get("text") or ""
                    if not text:
                        return {"ok": False, "message": "No text given."}
                    result = write_ndef(
                        rd, info, build_text_payload(text), force=bool(body.get("force"))
                    )
                    extra = {}
                elif action == "lock":
                    result = soft_lock(rd, info)
                    extra = {}
                elif action == "unlock":
                    result = soft_unlock(rd, info)
                    extra = {}
                elif action in ("mirror", "counter", "password"):
                    return self._configure(rd, info, action, body)
                else:
                    return {"ok": False, "message": f"Unknown action {action!r}."}

                logged = audit.record(
                    action, uid=info.uid.hex(":").upper(), product=info.product,
                    reader=rd.name, ok=result.ok, message=result.message,
                    source="web", **extra,
                )
                # A silent audit failure is worse than a noisy one: the whole
                # point is a record that can be trusted to be complete.
                if logged and "_log_error" in logged:
                    result.notes.append(
                        f"WARNING: could not write the audit log: {logged['_log_error']}")
                return {
                    "ok": result.ok,
                    "message": result.message,
                    "notes": result.notes,
                    "used": result.used,
                    "capacity": result.capacity,
                    "firstPage": result.first_page,
                    "lastPage": result.last_page,
                    **extra,
                }
            except ReaderError as exc:
                return {"ok": False, "message": str(exc)}
            except Exception as exc:  # pragma: no cover - defensive
                return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
            finally:
                rd.close()


    def refresh(self) -> None:
        """Re-read the tag after a write so the page shows what is now on it."""
        with self.io_lock:
            self._publish(self._read_once())


    def _configure(self, rd, info, action: str, body: dict) -> dict:
        """Apply a configuration-page change, refusing unrecoverable ones."""
        cfg_page = ntag.CONFIG_PAGE.get(info.product)
        if cfg_page is None:
            return {"ok": False, "message": f"{info.product} has no NTAG21x config pages."}
        raw = rd.read_config(cfg_page)
        cfg = ntag.parse_config(raw)
        notes: list[str] = []

        if action == "mirror":
            modes = {v: k for k, v in ntag.MIRROR_NAMES.items()}
            mode = body.get("mode", "off")
            if mode not in modes:
                return {"ok": False, "message": f"Unknown mirror mode {mode!r}."}
            cfg.mirror_conf = modes[mode]
            if mode == "off":
                cfg.mirror_page = 0
            else:
                page = int(body.get("page", 4))
                byte = int(body.get("byte", 0))
                if page <= 3:
                    return {"ok": False,
                            "message": "Mirror page must be above 3 (0-3 are UID, lock bits and CC)."}
                if not ntag.mirror_fits(page, byte, cfg.mirror_conf, info.last_user_page):
                    return {"ok": False, "message":
                            f"A {ntag.MIRROR_WIDTH[cfg.mirror_conf]}-character mirror at "
                            f"page {page} byte {byte} runs past user memory."}
                cfg.mirror_page, cfg.mirror_byte = page, byte

        elif action == "counter":
            cfg.nfc_cnt_en = bool(body.get("enabled"))
            cfg.nfc_cnt_pwd_prot = bool(body.get("passwordProtect"))

        else:  # password
            what = body.get("action", "status")
            if what == "disable":
                cfg.auth0, cfg.prot = ntag.AUTH0_DISABLED, False
            elif what == "set":
                try:
                    pwd = bytes.fromhex((body.get("password") or "").replace(":", ""))
                    pack = bytes.fromhex((body.get("pack") or "0000").replace(":", ""))
                except ValueError:
                    return {"ok": False, "message": "Password and pack must be hex."}
                if len(pwd) != 4:
                    return {"ok": False, "message": "Password must be 4 bytes (8 hex chars)."}
                if len(pack) != 2:
                    return {"ok": False, "message": "Pack must be 2 bytes (4 hex chars)."}
                cfg.auth0 = int(body.get("auth0", 0xFF))
                cfg.prot = bool(body.get("protectReads"))
                danger = ntag.protection_locks_out(cfg, info.last_user_page,
                                                   bool(self._can_auth))
                if danger and not body.get("acceptLockout"):
                    return {"ok": False, "message": "REFUSED: " + danger}
                rd.write_page(cfg_page + 2, pwd)
                rd.write_page(cfg_page + 3, pack + b"\x00\x00")
                notes.append("PWD and PACK written. They cannot be read back -- "
                             "write the password down.")
            else:
                return {"ok": False, "message": "Password action must be set or disable."}

        danger = ntag.protection_locks_out(cfg, info.last_user_page, bool(self._can_auth))
        if danger and not body.get("acceptLockout"):
            return {"ok": False, "message": "REFUSED: " + danger}

        p0, p1 = ntag.build_config_pages(cfg, raw)
        rd.write_page(cfg_page, p0)
        rd.write_page(cfg_page + 1, p1)
        back = ntag.parse_config(rd.read_config(cfg_page))

        # The configuration bytes store on any chip; whether the silicon acts
        # on them is another matter. Check a UID mirror actually happens.
        if action == "mirror" and back.mirror_conf in (ntag.MIRROR_UID, ntag.MIRROR_BOTH) and info.uid:
            start = back.mirror_page * PAGE_SIZE + back.mirror_byte
            first = start // PAGE_SIZE
            try:
                window = rd.read_pages(first, 4)[start - first * PAGE_SIZE:][:14]
                if window == info.uid.hex().upper().encode()[:14]:
                    notes.append("Verified: the tag is substituting its UID on read.")
                else:
                    notes.append("WARNING: configured, but the tag is NOT applying the "
                                 "mirror. Clones commonly store it and ignore it.")
            except ReaderError:
                pass

        audit.record(
            action, uid=info.uid.hex(":").upper(), product=info.product,
            reader="web", ok=True, source="web",
            mirror=back.mirroring, counter=back.nfc_cnt_en,
            auth0=back.auth0, cfglck=back.cfglck,
        )
        return {"ok": True, "message": "Configuration written.",
                "notes": notes + ntag.describe(back, info.last_user_page)}


class Handler(BaseHTTPRequestHandler):
    server_version = "wristband"
    # BaseHTTPRequestHandler defaults to HTTP/1.0, under which a browser's
    # EventSource will not stream: it buffers the whole response until the
    # connection closes, so no event ever fires. curl -N streams it anyway,
    # which makes this look fine from the command line and broken in a
    # browser. Every response here carries a Content-Length except the event
    # stream, which closes the connection instead, so 1.1 is safe throughout.
    protocol_version = "HTTP/1.1"
    monitor: TagMonitor  # set on the server instance

    def log_message(self, fmt, *args):  # quieter than the default
        if "/api/events" not in self.path:
            super().log_message(fmt, *args)

    # -- helpers ---------------------------------------------------------

    def _send_json(self, obj, status=200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, name: str) -> None:
        path = (STATIC / name).resolve()
        if not path.is_file() or STATIC.resolve() not in path.parents:
            self.send_error(404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:
        mon = self.server.monitor
        if self.path in ("/", "/index.html"):
            self._send_static("index.html")
        elif self.path == "/api/state":
            state, _ = mon.snapshot()
            self._send_json(state)
        elif self.path == "/api/events":
            self._stream_events(mon)
        elif self.path.startswith("/static/"):
            self._send_static(self.path[len("/static/"):])
        elif self.path.startswith("/api/history"):
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(self.path).query)
            limit = int((query.get("limit") or ["25"])[0])
            entries = audit.read(limit=limit, uid=(query.get("uid") or [""])[0])
            self._send_json({"entries": entries, "path": str(audit.default_path())})
        elif self.path == "/healthz":
            self._send_json({"ok": True})
        else:
            self.send_error(404)

    def _stream_events(self, mon: TagMonitor) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")  # stops nginx buffering the stream
        # An event stream has no Content-Length, so under HTTP/1.1 the
        # connection must close at the end rather than be reused.
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        state, version = mon.snapshot()
        try:
            self.wfile.write(f"data: {json.dumps(state)}\n\n".encode())
            self.wfile.flush()
            while True:
                state, new_version = mon.wait_for_change(version, timeout=15.0)
                if new_version != version:
                    version = new_version
                    self.wfile.write(f"data: {json.dumps(state)}\n\n".encode())
                else:
                    self.wfile.write(b": keepalive\n\n")  # keeps proxies from timing out
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser navigated away

    def do_POST(self) -> None:
        if not self.path.startswith("/api/"):
            self.send_error(404)
            return
        action = self.path[len("/api/"):]
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"ok": False, "message": "Malformed JSON."}, 400)
            return
        monitor = self.server.monitor
        result = monitor.perform(action, body)
        if result.get("ok"):
            # Nothing polls the tag any more, so the snapshot has to be
            # refreshed explicitly or the page would still show the old data.
            try:
                monitor.refresh()
            except Exception:
                pass
        self._send_json(result, 200 if result.get("ok") else 400)


def serve(host: str = "0.0.0.0", port: int = 8080, reader: str | None = None) -> int:
    monitor = TagMonitor(reader)
    monitor.start()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.monitor = monitor
    httpd.daemon_threads = True
    shown = "localhost" if host in ("0.0.0.0", "") else host
    print(f"wristband web UI on http://{shown}:{port}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        monitor.stop()
        httpd.server_close()
    return 0
