# Contributing

## Most useful: a reader report

Only one reader has been tested, an Alcor Link AK9567. Everything else in the
compatibility table is inference from the PC/SC spec, and that inference has
already been wrong once: the AK9567 rejects the standard 4-byte page write.

If you have a different reader, open an issue with the output of:

```bash
wristband readers
wristband detect
wristband capabilities
```

plus your USB ID from `lsusb` and whether writing worked. Phone results are
equally useful: platform, OS version, record type, and whether the tap raised
a notification.

## Tests

No hardware or `pyscard` needed:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

`node` is optional; without it the web UI tests skip.

## House rules

Anything irreversible needs a citation. Lock bit masks and config layouts come
from the NXP NTAG213/215/216 datasheet rev 3.2 with the section number in a
comment, because a wrong bit permanently ruins somebody's tag. Not from memory
and not from a forum post.

Verify writes by reading back. Status words are not enough; a tag leaving the
field mid-write can acknowledge pages that never landed.

Do not widen the default. A plain URI record is the only layout that works on
both iOS and Android, so changing the default record type needs a real iPhone
test first.
