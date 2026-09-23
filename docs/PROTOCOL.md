# How a URL gets onto a tag

Four layers, with the real bytes from an NTAG213.

```
NTAG213 memory          4-byte pages
  Type 2 TLVs           "here is a block of NDEF data, this long"
    NDEF message        a sequence of records
      record            one URI, scheme abbreviated
```

## 1. Memory

| Page | Contents |
|---|---|
| 0-1 | UID and check bytes, read-only |
| 2 | bytes 2-3 are the static lock bits |
| 3 | capability container |
| 4-39 | user memory, 144 bytes |
| 40 | dynamic lock bits |
| 41-44 | configuration, `PWD`, `PACK` |

Writing past page 39 hits the lock and config pages, which is how tags get
bricked. Every write here is bounded by the capability container.

### Capability container

```
E1 10 12 00
|  |  |  +- access: high nibble read, low nibble write. 0 = free.
|  |  +---- size / 8. 0x12 * 8 = 144 usable bytes
|  +------- version 1.0
+---------- magic: this tag is NDEF-formatted
```

The size byte is what distinguishes the chips, since all NTAG21x report as
"MIFARE Ultralight" over PC/SC. Setting the access byte to `0F` marks the tag
read-only, and the page is one-time programmable, so that cannot be undone.

## 2. TLVs

User memory is a sequence of type-length-value blocks. A factory tag:

```
01 03 A0 0C 34   03 00   FE
|                |       +- terminator
|                +--------- NDEF TLV, length 0
+-------------------------- lock-control TLV, length 3
```

**The NDEF TLV does not start at byte 0.** Here it starts at byte 5, after
whatever the factory put in front. `ndef_tlv_offset()` walks past types `00`,
`01`, `02` and `FD`. A length of `FF` means the real length is the next two
bytes, big-endian.

## 3. Record

```
D1 01 33 55 04 65 78 61 6D 70 6C 65 ...
|  |  |  |  |  +- "example.com/c/3b9c2f47-..."
|  |  |  |  +---- URI prefix code 04 = "https://"
|  |  |  +------- type "U"
|  |  +---------- payload length 0x33 = 51
|  +------------- type length 1
+---------------- header flags
```

Header bits: 7 MB (message begin), 6 ME (message end), 5 CF (chunked,
rejected on decode), 4 SR (short record, 1-byte length), 3 IL (ID present),
2-0 TNF (1 = NFC Forum well-known).

`D1` = MB + ME + SR + TNF 1: a single short well-known record.

### URI abbreviation

The first payload byte indexes a 36-entry table, so common schemes cost one
byte instead of eight. `04` is `https://`, `02` is `https://www.`, `05` is
`tel:`, `00` means no abbreviation.

Longest match wins: `https://www.example.com` must use `02`, not `04`.

## 4. The arithmetic

A 58-character URL costs exactly 58 bytes:

```
 58  URL length
 -8  "https://" replaced by one prefix byte
 +1  the prefix byte
 51  URI record payload
 +4  header, type length, payload length, type "U"
 55  NDEF message
 +3  TLV type, TLV length, terminator
 58  bytes of user memory
```

Plus the 5-byte factory TLV, that is 63 of 144 on an NTAG213.

## Reading it by hand

```bash
opensc-tool --reader 1 --send-apdu FF:CA:00:00:00    # UID
opensc-tool --reader 1 --send-apdu FF:B0:00:03:04    # capability container
opensc-tool --reader 1 --send-apdu FF:B0:00:04:10    # first 16 data bytes
```

`wristband dump` prints the same with annotations; `wristband read` decodes it.
