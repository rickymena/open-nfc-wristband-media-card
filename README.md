# open-nfc-wristband-media-card

Write a URL to an NFC wristband, sticker or card from the command line or a
browser, using any PC/SC contactless reader.

```console
$ wristband write-url https://example.com/me
URL       : https://example.com/me
Tag       : NTAG213
Preserving 5 bytes of existing TLVs before the NDEF area.
Writing 29 bytes to pages 5-13...
Verified. 34/144 bytes used.
```

Commercial tap-to-share bracelets encode a link to the vendor's server, which
redirects to your profile. That gives them the scan log and a kill switch. A
raw NTAG213 tag costs a few dollars and holds your URL directly.

## You need somewhere to host the URL

A tag stores a pointer, not content. 144 bytes holds a URL and nothing else,
so whatever it points at must be served by something you control: a VPS, static
hosting on your own domain, or a route on a site you already run.

Own the domain, not just the hosting. A tag already stuck to something cannot
be edited, and a shortener link hands the redirect back to a third party, which
is the thing this avoids. Keep the URL short, since every character is a byte,
and keep the path stable so you can change what sits behind it instead.

## Hardware

Tested against these. ASINs are listed because links rot.

| | Item | ASIN |
|---|---|---|
| Reader | [CAC NFC Smart Card Reader, dual interface USB](https://www.amazon.com/dp/B0FNWN6WTT) | `B0FNWN6WTT` |
| Wristband | [HECERE NTAG213 silicone wristband](https://www.amazon.com/dp/B0BN14VZSX) | `B0BN14VZSX` |
| Stickers | [20x NTAG213 transparent stickers](https://www.amazon.com/dp/B0DQV8NJ5Z) | `B0DQV8NJ5Z` |

Not endorsements. Each taught us something the product page does not say:

- The reader is sold as a military CAC card reader. It enumerates as an Alcor
  Link AK9567 and works, but it rejects the standard 4-byte page write and
  cannot send native NTAG commands, so passwords are unusable on it.
- The wristband is genuine NXP. Everything works.
- The stickers are clones, not NXP. Mirroring silently does nothing, and their
  small antennas need more field strength than the reader reliably delivers.

Details in [docs/HARDWARE.md](docs/HARDWARE.md). Any PC/SC reader and any NFC
Forum Type 2 tag (NTAG213/215/216, MIFARE Ultralight) should work.

## Install

`pyscard` is a C extension, so it needs system packages even in a virtualenv.

```bash
# Fedora
sudo dnf install pcsc-lite pcsc-lite-ccid pcsc-lite-devel swig gcc
sudo systemctl enable --now pcscd.socket

# Debian / Ubuntu
sudo apt install pcscd libpcsclite1 libpcsclite-dev swig gcc
sudo systemctl enable --now pcscd
```

```bash
git clone https://github.com/rickymena/open-nfc-wristband-media-card
cd open-nfc-wristband-media-card
python3 -m venv .venv && .venv/bin/pip install -e .
```

## Usage

```bash
wristband detect                        # what is on the reader
wristband write-url https://example.com # write a URL
wristband write-url URL --count 20      # write a whole pack
wristband read                          # read it back
wristband dump                          # raw hex, page by page
wristband lock --soft --yes             # read-only to phones
wristband history                       # audit log of writes and locks
wristband serve                         # browser UI on :8080
```

Also `readers`, `write-text`, `format`, `erase`, `unlock`, `capabilities`,
`config`, `mirror`, `counter`, `password`. `--help` on any of them.

`write-url` falls back to `$WRISTBAND_URL`. `--ref nfc` appends a path segment
so tag taps are distinguishable in your logs.

## Web UI

```bash
podman build -t wristband -f Containerfile .
podman run -d -p 8080:8080 \
  -v /run/pcscd/pcscd.comm:/run/pcscd/pcscd.comm \
  --security-opt label=disable wristband
```

Open http://127.0.0.1:8080, not `localhost`. The container never touches the
USB device; it uses the host's pcscd socket and runs unprivileged as a non-root
user. See [docs/WEBUI.md](docs/WEBUI.md).

## Three things that will bite you

- A title or icon needs a Smart Poster record, and **iOS ignores those
  entirely**. An iPhone shows no notification at all. Plain URI records are the
  default for this reason. [Details](docs/COMPATIBILITY.md)
- Locking cannot be undone. `lock --soft` stops phones writing; `lock` sets the
  hardware lock bits and is final. The capability container is one-time
  programmable, so even the soft flag can never be cleared on genuine NXP.
- NFC does not work through metal. A sticker that reads on a desk goes dead on
  a laptop lid. That needs an on-metal tag with a ferrite layer.

## Capacity

Usable capacity is what the tag's capability container reports, which is less
than the figure sellers advertise.

| Chip | Sold as | Usable |
|---|---|---|
| MIFARE Ultralight | 64 B | 48 bytes |
| NTAG213 | 168-180 B | 144 bytes |
| NTAG215 | 504-540 B | 504 bytes |
| NTAG216 | 888-924 B | 888 bytes |

A 58-character URL costs 58 bytes: its length, minus 8 for the abbreviated
scheme, plus 7 of framing.

## Security

Anyone who taps your tag can read it. Encode a link you would hand to a
stranger. If your link hub issues separate private and public URLs, the tag
gets the public one.

## Documentation

| | |
|---|---|
| [COMPATIBILITY.md](docs/COMPATIBILITY.md) | What each phone does with each record type |
| [HARDWARE.md](docs/HARDWARE.md) | Readers, tags, the 16-byte write quirk, troubleshooting |
| [PROTOCOL.md](docs/PROTOCOL.md) | Byte-level walkthrough of a tag's memory |
| [MIRRORING.md](docs/MIRRORING.md) | ASCII mirroring and the NFC counter |
| [PASSWORD.md](docs/PASSWORD.md) | Password protection and the lockout guard |
| [WEBUI.md](docs/WEBUI.md) | Container, HTTP API, audit log |

## Design

| Module | Does |
|---|---|
| `ndef.py` | NDEF records and Type 2 TLVs. No dependencies. |
| `tags.py` | Capability container, write bounds, lock masks. |
| `ntag.py` | Configuration pages: mirroring, counter, password. |
| `apdu.py` | APDU construction, including per-reader quirks. |
| `writer.py` | The verified write path, shared by CLI and web. |
| `reader.py` | pyscard/PC/SC transport. |
| `audit.py` | Append-only JSON Lines log. |

`pyscard` is imported only by `reader.py`, so everything else is testable with
no hardware attached. Writes are verified by reading back, existing TLVs are
preserved, and writes are bounded by the capability container so the tool
cannot run into the lock pages and brick a tag.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

154 tests. No reader, tag or `pyscard` required. CI runs 3.9, 3.11 and 3.13.

## Contributing

Only one reader has been tested, so reader and phone reports are the most
useful thing you can send. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT, see [LICENSE](LICENSE).
