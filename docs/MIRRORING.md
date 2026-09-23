# ASCII mirroring and the NFC counter

Two NTAG21x features that need no native commands. The configuration pages are
ordinary memory, so any PC/SC reader can set them.

## What mirroring does

The chip substitutes its own UID and/or read counter, as ASCII hex, into the
data it returns. The bytes on the tag never change; the substitution happens on
every read.

That means **twenty tags can carry identical data and still resolve to twenty
different URLs**:

```
written to the tag:   https://example.com/c/TOKEN?t=00000000000000
what a phone reads:   https://example.com/c/TOKEN?t=04596CE2D22091
```

No per-tag writing, no database of which sticker went where.

| Mode | Characters | Content |
|---|---|---|
| `uid` | 14 | 7-byte UID as hex |
| `counter` | 6 | 3-byte counter as hex |
| `both` | 21 | UID, separator, counter |

## Setting it up

The placeholder must be exactly that many characters, because the chip
overwrites in place.

```bash
wristband write-url 'https://example.com/c/TOKEN?t=00000000000000'
wristband mirror uid --page 15 --byte 0
```

To find the position: the data area starts at page 4, the factory lock-control
TLV takes bytes 0-4, the NDEF TLV header 5-6, and the record header 7-11. So
the first URL character is byte 12, and

```
start = 12 + (URL length after the scheme) - (mirror width)
page  = 4 + start // 4
byte  = start % 4
```

`wristband dump` shows the pages with ASCII alongside, which is quicker. The
tool refuses a page at or below 3, and refuses a mirror that would run past
user memory.

After configuring, it reads the target region back and checks the UID actually
appears there, reporting either `Verified` or a warning.

## Clones do not support it

Measured, not assumed. A clone (UID `53:77:C2:74:A3:00:01`) accepted the
configuration perfectly, reading back as `44 00 0F FF`, which decodes exactly
as intended. Across three fresh RF sessions it still returned the literal
placeholder and never its UID.

The configuration bytes store on any chip because they are just memory.
Whether the silicon acts on them is a different question. Check with
`wristband capabilities`: if the UID does not start with `04`, mirroring, the
counter, `GET_VERSION` and password authentication may all be absent. Basic
NDEF read/write and locking still work.

There is no way to tell from a product listing. Check the UID of the first tag
out of the pack.

## The NFC counter

A 3-byte counter that increments on the first read after each power-on.

```bash
wristband counter on
wristband counter off
```

Reading the raw value needs the native `READ_CNT` command, which most CCID
readers cannot send. Mirroring it into the URL sidesteps that, because the
substitution happens on an ordinary read:

```bash
wristband mirror counter --page 16 --byte 2
```

## Strong modulation

Bit 2 of the MIRROR byte, on by default. It raises modulation amplitude, which
helps weak readers. `wristband config` reports it; there is rarely a reason to
change it.
