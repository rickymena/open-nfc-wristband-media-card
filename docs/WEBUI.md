# Web UI and container

A browser UI for writing tags, packaged as a rootless container. Plain HTML,
CSS and JavaScript, no framework; the only runtime dependency is `pyscard`.

## Running it

```bash
podman build -t wristband -f Containerfile .

podman run -d --name wristband -p 8080:8080 \
  -v /run/pcscd/pcscd.comm:/run/pcscd/pcscd.comm \
  -v "$PWD/audit:/data" \
  --userns=keep-id:uid=10001,gid=10001 \
  --security-opt label=disable \
  wristband
```

Open **http://127.0.0.1:8080**. Without a container: `wristband serve`.

Use `127.0.0.1`, not `localhost`. Where `localhost` resolves to `::1` only, as
on Fedora, the page will not load because podman forwards IPv4. Check with
`getent ahosts localhost`.

## How it reaches the reader

The container does not get the USB device. `pcscd` runs on the host and only
its socket is mounted. That socket is world read/write, so the container's
non-root user needs no capabilities, no `--privileged` and no device
passthrough. The image ships `libpcsclite1`, not the daemon. It also means the
reader can be replugged without the container caring.

`--security-opt label=disable` is needed because SELinux labels that socket
`pcscd_var_run_t`. Mounting it `:z` would relabel the **host's** socket and can
break pcscd for everything else.

`--userns=keep-id:uid=10001,gid=10001` maps your host user onto the
container's uid so the audit volume is writable. Without it the log silently
fails to write.

## Live detection

Presence comes from `SCardGetStatusChange`, which reports whether a card is in
the field **without connecting**. That matters: pyscard's `disconnect()` issues
`SCardDisconnect(SCARD_UNPOWER_CARD)`, which powers the tag down. Polling by
connect/disconnect resets the tag several times a second, and any concurrent
write dies with "Card was reset" or a bare `63 00`.

The browser subscribes to `/api/events`, a Server-Sent Events stream. Only
changes are sent, with a keepalive every 15 s. All reader access goes through
one lock, because a card connection is exclusive.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | the page |
| `GET` | `/api/state` | current snapshot |
| `GET` | `/api/events` | SSE stream of changes |
| `GET` | `/api/history` | audit log entries |
| `GET` | `/healthz` | `{"ok": true}` |
| `POST` | `/api/write-url` | `{url, ref?, title?, force?}` |
| `POST` | `/api/write-text` | `{text, force?}` |
| `POST` | `/api/lock`, `/api/unlock` | read-only flag |
| `POST` | `/api/mirror`, `/api/counter`, `/api/password` | configuration |

`genuine` in a state snapshot is byte 0 of the UID: `04` is NXP, anything else
is a clone.

## Audit log

Every write, lock and configuration change is appended to a JSON Lines file,
one self-contained object per line.

```json
{"ts":"2026-09-23T02:11:04Z","action":"write-url","ok":true,
 "uid":"53:78:C2:74:A3:00:01","product":"NTAG213","url":"https://example.com"}
```

Failures are recorded too. Default location is
`~/.local/share/wristband/audit.jsonl`, overridden by `$WRISTBAND_AUDIT_LOG`
and disabled by `$WRISTBAND_NO_AUDIT`. Read it with `wristband history`,
`--uid` and `--action` filters, or the History panel.

**Passwords are never recorded.** `PWD` and `PACK` cannot be read back off a
tag, so a log holding them would be the only copy and a liability. They are
redacted where the entry is built.

## What the UI will not do quietly

- A title warns that it forces a Smart Poster record, which iPhones ignore.
- Locking asks for confirmation and says plainly that it cannot be undone.
- Every write is read back and compared before it reports success.

## Image notes

Two stages: the first builds the `pyscard` wheel with `gcc`, `libc6-dev`,
`swig` and `libpcsclite-dev`; the runtime stage carries only `libpcsclite1`.
No compiler ships in the final image.

`HEALTHCHECK` lives in `compose.yaml`, not the Containerfile, because podman
builds OCI images by default and the OCI spec has no healthcheck field, so one
there is silently dropped.
