# SpiceBot

A friendly spice and herb guide: 126 spices and herbs, their chemistry (structures, formulas, molar masses),
flavour charts, PDF books with page citations, and a chatbot that works offline. With internet it also adds
details from Wikipedia and PubChem and saves them for later.

- **Website:** https://oonnosfjsjfie.github.io/spicebot/
- **Android app:** https://oonnosfjsjfie.github.io/spicebot/SpiceBot.apk (also the "Get the Android app" button on the website)

## How the app works

Like the Neutrix app, the Android app opens the live website, so **every change to the website reaches the app
straight away**, with no new install. The website saves a copy of itself on the phone (`sw.js`), so the app also
works offline. If the phone is offline on the very first launch, the app opens the copy built into it instead.

A new APK is only needed if the app itself changes (its name, icon or Android code).

## Files

| File | What it is |
| --- | --- |
| `index.html` | The whole SpiceBot website and chatbot, in one file |
| `sw.js` | Keeps SpiceBot working offline; always fetches the newest version first |
| `manifest.webmanifest`, `*.png` | App name and icons for "Add to Home screen" |
| `SpiceBot.apk` | The Android app |
| `android/` | The app's source code (Java WebView app, package `com.spicebot.app`) |

## Updating

- **Website and chatbot:** replace `index.html`. The website and the app both update by themselves.
- **App:** rebuild `SpiceBot.apk` from `android/` and replace it. It must be signed with the same private key
  (`spicebot.jks`, kept outside this repository) so it installs over the old version.
