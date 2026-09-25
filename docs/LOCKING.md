# Locking

A Type 2 tag can be made read-only in two ways. They look the same to a phone
and very different to a PC/SC reader.

| | Soft lock (`lock --soft`) | Hard lock (`lock`) |
|---|---|---|
| What it sets | CC byte 3, the write-access nibble, to `0F` | static lock bits on page 2, dynamic lock bits on the chip's lock page |
| Phones | refuse to write | refuse to write |
| This reader | can still rewrite the data | cannot write the data |
| Undo | no, on genuine NXP | no, on any chip |

## Soft lock

The capability container (page 3) is one-time programmable on genuine NXP
silicon: a bit set to 1 stays 1. Once the access byte reads `0F`, `unlock`
cannot clear it. Clone chips often do not enforce this, so `unlock` tries and
reports what it reads back.

```console
$ wristband unlock
Tag       : NTAG213  UID 04:7F:62:DA:CA:20:90
  The capability container is one-time programmable, so the access byte can never be cleared once set.
  The tag's data is still rewritable with this reader -- only phones honour the read-only flag.
error: Could not unlock: CC still reads E1 10 12 0F.
```

The flag is only a request to NFC Forum readers. Phones honour it. The pages
holding the NDEF message are still writable, so a soft-locked tag can be
rewritten from here:

```bash
wristband write-url https://example.com --force
```

In the web UI, a soft-locked tag shows **read-only to phones** and the write
button changes to **Rewrite soft-locked tag**, with a confirmation.

The tag stays read-only to phones afterwards. That is usually what you want
from a wristband you hand out: nobody can overwrite it by tapping it, and you
can still fix a typo.

## Hard lock

`lock` sets the static lock bits (`L-CC`, `L4`..`L15`) and the dynamic lock
bits covering the rest of user memory. The chip then rejects writes to those
pages from any reader. There is no way back.

Before a forced write, the tool reads both sets of lock bits. If any are set,
it refuses rather than sending writes the chip will reject. It also refuses on
a chip whose lock layout it does not know.

Masks and page numbers are in `tags.py`, with datasheet references.
[PROTOCOL.md](PROTOCOL.md) shows where the lock pages sit in memory.

## Before you lock

Tap the tag with an iPhone and an Android phone first. A lock is permanent,
and a wrong URL on a hard-locked tag means a new tag.
