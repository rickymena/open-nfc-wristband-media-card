"""Append-only audit log of everything written to a tag.

Tags are physical objects that leave the room. Once a bracelet is handed over
there is no way to ask it what it used to hold, or when it was locked, or
which of twenty stickers got which URL. This records that as it happens.

The format is JSON Lines: one self-contained JSON object per line, appended
and never rewritten. That survives an interrupted write (a truncated last line
is the only damage possible), can be tailed, and needs no parser to read.

Secrets are never recorded. A password write logs that it happened and what
scope it applied, never PWD or PACK -- those cannot be read back off the tag,
so a log holding them would be the only copy and a liability.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

ENV_PATH = "WRISTBAND_AUDIT_LOG"
ENV_DISABLE = "WRISTBAND_NO_AUDIT"

# Keys that must never reach the log, whatever a caller passes.
_REDACT = {"password", "pwd", "pack", "secret", "key"}


def default_path() -> Path:
    """Where the log lives unless told otherwise.

    Follows XDG so it survives reinstalls and is not inside the repo, where it
    would risk being committed.
    """
    override = os.environ.get(ENV_PATH)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base).expanduser() / "wristband" / "audit.jsonl"


def enabled() -> bool:
    return os.environ.get(ENV_DISABLE, "").lower() not in ("1", "true", "yes")


def _scrub(details: dict) -> dict:
    """Drop anything secret, and shorten anything unbounded."""
    out = {}
    for key, value in details.items():
        if key.lower() in _REDACT:
            out[key] = "<redacted>"
        elif isinstance(value, (bytes, bytearray)):
            out[key] = value.hex().upper()
        elif isinstance(value, str) and len(value) > 500:
            out[key] = value[:500] + "..."
        else:
            out[key] = value
    return out


def record(action: str, *, uid: str = "", product: str = "", reader: str = "",
           ok: bool = True, message: str = "", path: Path | None = None,
           **details) -> dict | None:
    """Append one event. Returns the entry, or None if logging is off.

    Never raises: an audit log that breaks the tool it audits is worse than no
    audit log, so a failure to write is swallowed after being noted in the
    returned entry.
    """
    if not enabled():
        return None
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": action,
        "ok": ok,
    }
    if uid:
        entry["uid"] = uid
    if product:
        entry["product"] = product
    if reader:
        entry["reader"] = reader
    if message:
        entry["message"] = message
    entry.update(_scrub(details))

    target = path or default_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:  # pragma: no cover - disk/permission specific
        entry["_log_error"] = str(exc)
    return entry


def read(path: Path | None = None, limit: int = 0, uid: str = "",
         action: str = "") -> list[dict]:
    """Read entries back, newest last. Malformed lines are skipped."""
    target = path or default_path()
    if not target.is_file():
        return []
    out: list[dict] = []
    with target.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # truncated final line after an interrupted write
            if uid and entry.get("uid", "").upper() != uid.upper():
                continue
            if action and entry.get("action") != action:
                continue
            out.append(entry)
    return out[-limit:] if limit else out


def summarise(entry: dict) -> str:
    """One readable line for a terminal."""
    mark = "ok  " if entry.get("ok") else "FAIL"
    bits = [entry.get("ts", "?"), mark, entry.get("action", "?")]
    if entry.get("uid"):
        bits.append(entry["uid"])
    for key in ("url", "text", "mode", "auth0", "state"):
        if key in entry:
            bits.append(f"{key}={entry[key]}")
    if not entry.get("ok") and entry.get("message"):
        bits.append(f"-- {entry['message'][:60]}")
    return "  ".join(str(b) for b in bits)
