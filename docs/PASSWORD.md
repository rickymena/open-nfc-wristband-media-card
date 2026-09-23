# Password protection

NTAG21x have a 32-bit password, separate from the read-only flag and the lock
bits. It is the only protection that is both hardware-enforced and reversible.

This tool implements it and refuses to enable it unless your reader can
authenticate. That guard is the point of this document.

| Mechanism | Stops a phone writing | Reversible |
|---|---|---|
| CC read-only flag (`lock --soft`) | yes | no, the CC is one-time programmable |
| Lock bits (`lock`) | yes | never |
| 32-bit password | yes | yes, with the password |
| `CFGLCK` | config only | never |

## Check your reader first

```bash
wristband capabilities
```

Setting a password and being able to use one are different problems.
Authenticating needs the native `PWD_AUTH` command (`1B` + 4 bytes), which has
no PC/SC mapping. The configuration pages are plain memory, so **any** reader
can enable protection and then be permanently unable to write the tag.

Three routes exist to send a native command. The reader tested here supports
none:

| Route | Result on the AK9567 |
|---|---|
| `FF 00 00 00` (ACR122U escape) | returns unrelated data, not a transmit channel |
| `FF C2` transparent session | `6A 82`, unsupported. The only standard route. |
| CCID escape | advertised, but ignores the payload |

A genuine ACR122U supports the first.

## Layout

| Page (NTAG213) | Field |
|---|---|
| 41 byte 3 | `AUTH0`, first protected page. `FF` disables. |
| 42 byte 0 | `ACCESS`: bit 7 `PROT`, bit 6 `CFGLCK`, bits 0-2 `AUTHLIM` |
| 43 | `PWD`, 4 bytes |
| 44 bytes 0-1 | `PACK`, 2 bytes |

NTAG215 uses 131-134, NTAG216 227-230. `PWD` and `PACK` read back as zero;
they are write-only. A forgotten password is not recoverable.

## Usage

```bash
wristband password status
wristband password set --password DEADBEEF --pack 1122 --auth0 4
wristband password disable
```

`--auth0` is the first protected page: `4` protects all user memory, `255`
disables. `--protect-reads` guards reads too, which is usually wrong for a tag
whose whole purpose is being read.

## Three irreversible traps

Refused unless `--accept-lockout` is passed:

1. Enabling protection on a reader that cannot authenticate.
2. `CFGLCK`, which freezes the configuration pages forever.
3. `AUTHLIM` above 0. Exceed the limit and the tag refuses authentication
   permanently, leaving the protected pages read-only for good.

## It is not cryptography

The password goes over the air in the clear. Anyone who observes one
successful authentication has it. It deters casual rewriting and keeps no
secrets, so do not reuse one from anywhere else.

For a tag you hand to strangers, `lock --soft` is usually the better tool.

## If your reader cannot authenticate

Set it with [NFC Tools](https://www.wakdev.com/en/apps/nfc-tools.html) on a
phone, whose NFC stack exposes native commands that a CCID reader does not.
Note that clones frequently omit password support entirely.
