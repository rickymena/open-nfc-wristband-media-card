# Hardware

## Readers

Any PC/SC (CCID) reader with a contactless slot should work. This tool uses
only standard PC/SC part 3 storage-card APDUs, not vendor escapes.

| Reader | USB ID | Status |
|---|---|---|
| Alcor Link AK9567 | `2ce3:9567` | Tested. Needs the 16-byte write form. |
| ACS ACR122U | `072f:2200` | Expected to work. Widely cloned. |
| Identiv uTrust 3700F | `04e6:5790` | Expected to work. |
| SCM SCL3711 | `04e6:5591` | Expected to work. |

Only the AK9567 has been tested. The rest are listed because they implement
the same mapping, not because anyone verified them here.

A dual-interface reader shows two slots. The contact one never sees a tag:

```
0    Alcor Link AK9567 00 00
1    Alcor Link AK9567 [Contactless Card Reader] 01 00      <- this one
```

The contactless slot is picked automatically by name. Override with
`--reader`. If nothing is listed, `pcscd` is not running:

```bash
sudo systemctl enable --now pcscd.socket
```

## The 16-byte write form

The spec says a Type 2 page write is `FF D6 00 <page> 04 <4 bytes>`. The
AK9567 rejects that with `63 00` on every page and accepts only:

```
FF D6 00 <page> 10 <16 bytes>
```

It writes the first 4 bytes to the target page and discards the other 12,
verified by writing 16 distinct bytes to page 24 and reading pages 24-27 back.
Because that discard is not guaranteed elsewhere, the padding is built from
the tag's current contents, so a reader that honours all 16 bytes rewrites the
neighbours with what they already hold.

`reader.py` tries the 4-byte form first and falls back on `63 00`, caching
whichever works. Symptom to recognise: **reads fine, every write returns
`63 00`.**

## Tags

Needs an NFC Forum Type 2 tag. MIFARE **Classic** is not one and is not
supported.

| Chip | Usable NDEF |
|---|---|
| MIFARE Ultralight | 48 B |
| NTAG213 | 144 B |
| NTAG215 | 504 B |
| NTAG216 | 888 B |

Wristbands, stickers, cards and keyfobs are all the same chip, so one command
writes any of them. `--count N` writes a pack, remembering UIDs so a tag left
on the reader is not written twice.

### Genuine chips and clones

Byte 0 of a UID is the manufacturer code. NXP is `04`; anything else is a
clone.

```
04:59:6C:E2:D2:20:91    genuine NXP
53:9E:C2:74:A3:00:01    clone
```

Clones handle basic NDEF read/write and locking fine. They commonly omit
`GET_VERSION`, the NFC counter, password authentication, ASCII mirroring, and
reliable one-time-programmable behaviour. Do not assume a lock is permanent on
one. `wristband capabilities` reports the silicon.

### A factory NTAG213, out of the bag

```
ATR     3B 8F 80 01 80 4F 0C A0 00 00 03 06 03 00 03 00 00 00 00 68
UID     04:59:6C:E2:D2:20:91
page 2  ... 00 00                     static lock bytes: unlocked
page 3  E1 10 12 00                   CC: NDEF, 144 bytes, writable
page 4+ 01 03 A0 0C 34  03 00  FE     lock-control TLV, empty NDEF TLV, end
```

Note the lock-control TLV **before** the NDEF TLV. The message starts at byte
5 of the data area, not byte 0. Tools that assume page 4 destroy it.

The ATR card name is `00 03`, which PC/SC calls "MIFARE Ultralight"; every
NTAG21x reports that. The CC size byte distinguishes them: `12` NTAG213, `3E`
NTAG215, `6D` NTAG216.

## When reads work and writes never do

Every write returns `63 00`, every read succeeds. This happened on a
factory-fresh, never-written, never-locked sticker, minutes after the same
reader wrote a wristband without complaint.

That rules out wear, locking, a dead tag, a dead reader and software. What is
left is **RF power margin**: an EEPROM write draws far more energy than a
read, and a wristband's antenna is much larger than a sticker's.

1. Try a physically larger tag. If a wristband writes where a sticker will
   not, it is power, not a broken tag.
2. Centre the tag on the antenna ring under the logo and press it flat.
3. Unplug the reader, leave it a few minutes, plug it back in.
4. Use a rear USB port or a powered hub.

Do not conclude the tag is dead. That was the first conclusion reached here
and it was wrong.

## Stickers and metal

NFC couples inductively at 13.56 MHz. Metal behind the antenna absorbs and
detunes the field, and the tag simply will not read. A sticker that works on
paper goes dead on a laptop lid, a phone back or a steel door.

Buy **on-metal** tags, which carry a ferrite layer, or leave a few millimetres
of non-metal spacing. Ordinary transparent stickers have no ferrite layer.

## Buying

Search for the chip, not the shape. Listings quote total memory (168 or 180
bytes for NTAG213); the NDEF-usable figure is 144. Check the listing says
"NFC Forum Type 2", "NTAG" or "Ultralight". For anything touching metal, buy
on-metal tags.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `No PC/SC readers found` | `pcscd` not running, or reader unplugged |
| `No tag detected` | Tag off-centre. The antenna is a ring under the logo. |
| Reads work, all writes `63 00` | 16-byte write form, or RF power margin (above) |
| `SW=6A81` | Reader does not support this operation on this tag |
| `verification failed` | Tag moved mid-write. Hold it still and retry. |
| Write succeeds, phone shows nothing | Try `wristband format` |
