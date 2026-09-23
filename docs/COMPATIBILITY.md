# Phone compatibility

**Write a plain URI record.** It is the default here and the only layout that
works everywhere.

| Record | Android | iPhone (background reading) |
|---|---|---|
| URI (`U`) | notification, opens link | notification, opens link |
| Smart Poster (`Sp`) | notification, uses the title | **ignored, nothing happens** |
| Text (`T`) | shown by NFC apps | no notification |

iOS only processes the **first URL record in the message**, and it must be at
the top level. A URI nested inside a Smart Poster is never found, even though
the nested record is perfectly valid. Found on real hardware: the same
wristband triggered normally with a URI record and did nothing once wrapped in
a Smart Poster.

Reference:
[GoToTags on iOS background reading](https://gototags.com/help/ios/nfc/reading/background).

An iPhone can still read a Smart Poster inside an app like NFC Tools. It is
the automatic background notification, the thing that makes a tag feel like it
works, that fails.

## Controlling the title and icon

On iPhone you cannot set them from the tag. The notification shows the domain,
and everything after the tap comes from your site. That is the better place
anyway: it costs no tag capacity and works on every phone.

```html
<title>Your Name</title>
<link rel="apple-touch-icon" href="/apple-touch-icon.png" />
<meta property="og:image" content="https://example.com/og-image.png" />
```

A short, readable domain is worth more than any NDEF title.

`--title` stays supported for Android-only deployments and kiosk software that
honours Smart Posters. It warns, because reaching for it silently costs you
every iPhone user.

## When a tag does not trigger

1. `wristband read` to confirm what is actually on it.
2. Check the record type. A Smart Poster explains an iPhone doing nothing.
3. Confirm the URL has a scheme. `example.com` is not a URL.
4. iOS background reading is off in Airplane Mode, in Camera, or during an
   active NFC session in another app.
5. Hold the **top edge** of an iPhone to the tag. The antenna is not in the
   middle of the back.
