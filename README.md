# deFrost

**Protect your browser from the FROST tracking exploit — one click, no technical knowledge required.**

![Status](https://img.shields.io/badge/status-alpha-orange)
![Platform](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## What is FROST?

FROST (Fingerprinting Remotely using OPFS-based SSD Timing) is a browser-based
side-channel attack disclosed by researchers at Graz University of Technology in
May 2026. A malicious webpage can silently identify which websites you are
visiting and which applications you have open — with up to 96% accuracy — using
only standard browser APIs and no special permissions.

It works by exploiting SSD read contention. When your browser loads a page, it
reads cached assets and profile data from your SSD. A malicious tab can detect
those reads by timing its own SSD access and measuring when latency spikes. The
timing pattern reveals what you are doing.

**No browser vendor has shipped a fix.** Google classifies it as out of scope.
Mozilla acknowledged it and has taken no action. Apple has not responded.

More detail: [Graz University of Technology FROST disclosure](https://github.com/o51r15/defrost)

---

## How deFrost Fixes It

The attack depends entirely on your browser reading its profile from an SSD.
deFrost moves the browser profile into RAM. Browser reads come from memory —
no SSD involvement, no timing signal, no fingerprinting possible.

When you activate protection:

1. Your browser closes cleanly
2. The profile is copied to a RAM disk
3. A transparent redirect (NTFS junction point) points your browser at the RAM copy
4. Your browser relaunches — everything works exactly as before
5. The FROST attack surface is completely eliminated for that session

When you deactivate, all changes sync back to disk and your profile is restored.

---

## Features

- **One-click activation** from a clean local web interface
- **Live progress window** showing exactly what is happening at each step
- **Automatic browser close and relaunch** — no manual steps
- **Cache-excluded copy** — browser cache is regenerable and enormous; deFrost
  only moves your real profile data (cookies, history, passwords, extensions)
  reducing RAM requirements dramatically. Cache rebuilds in RAM automatically
  during the session.
- **Size-threshold sync** — changes flush to disk when the write buffer fills,
  not on a fixed timer. Your data is always safe
- **Crash recovery** — detects unclean shutdown on next launch and restores
  the profile automatically
- **Portable** — no installer. Drop the folder anywhere and run it
- **Open source** — read exactly what it does to your browser profile

---

## Supported Browsers

| Browser | Status |
|---------|--------|
| Google Chrome | ✅ Tested |
| Mozilla Firefox | ✅ Tested |
| Microsoft Edge | 🔲 Implemented, untested |
| DuckDuckGo Browser | 🔲 Implemented, untested |
| Brave | 🔲 Implemented, untested |
| Vivaldi | 🔲 Implemented, untested |
| Opera | 🔲 Implemented, untested |

---

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or later (for running from source)
- Sufficient free RAM:
  - Firefox: typically 300–600 MB
  - Chrome: typically 1–3 GB (varies by extensions and storage usage)
- Administrator privileges (required for RAM disk creation and junction points)

---

## Installation

### Option A — Run from source (current, pre-release)

```bash
git clone https://github.com/o51r15/defrost.git
cd defrost
pip install -r requirements.txt
python src/tray.py
```

A UAC prompt will appear on first launch. This is expected and required — deFrost
needs administrator rights to create the RAM disk and junction points.

### Option B — Portable bundle (coming soon)

A pre-built portable `.exe` bundle will be provided when the project exits alpha.
The bundle will include all dependencies and auto-install the ImDisk driver on
first use.

---

## Usage

1. **Launch deFrost** — a shield icon appears in your system tray
2. **Open the dashboard** — double-click the tray icon or navigate to
   `http://127.0.0.1:7375` in any browser
3. **Go to Setup** — select your browser and profile
4. **Review RAM requirements** — the setup page shows your profile size
   (excluding cache) and the total RAM needed
5. **Click Activate Protection** — confirm the browser restart prompt
6. deFrost closes your browser, copies the profile to RAM, and relaunches it
7. The dashboard shows 🛡️ **PROTECTED** — you are covered

To deactivate, click **Deactivate Protection** on the dashboard. Your browser
closes, changes sync to disk, and it relaunches normally.

---

## How the Write Buffer Works

While protected, your profile is in RAM. New writes (cookies, history updates,
downloads, extension changes) accumulate in the RAM disk's write buffer. When
the buffer reaches its threshold (200 MB by default), deFrost flushes only the
changed files back to disk using Robocopy. Your profile on disk is always
recoverable, and the buffer size is configurable in the setup page.

This means:
- Periodic disk writes do not defeat the protection — they are bulk sequential
  writes, not the read contention pattern FROST looks for
- If your machine loses power, at most one buffer's worth of browsing changes
  is lost. Your core profile data (bookmarks, saved passwords, extensions) is
  safe because those files are small and sync frequently

---

## Architecture

```
src/
├── tray.py          Main entry point. pystray tray icon on main thread.
│                    Flask starts as a daemon thread.
├── app.py           Flask web UI (127.0.0.1:7375 only — not network accessible).
│                    Background threads for activation and deactivation.
│                    Polling endpoints for live status.
├── core.py          RAM disk lifecycle. Activation and deactivation sequences.
│                    ImDisk driver detection and auto-install.
│                    Browser process management.
├── browsers.py      Browser detection via known filesystem paths.
│                    Chromium Preferences JSON parsing for profile names.
│                    Firefox profiles.ini parsing.
│                    Fast size scan (UI) and accurate scan (activation).
├── sync.py          Delta sync engine. Robocopy wrapper.
│                    Background monitor thread watching write buffer size.
├── config.py        JSON config (config.json next to exe).
│                    Session state with crash recovery detection.
└── status_window.py Native tkinter floating progress window.
                     Survives browser termination. Shows live step-by-step log.

assets/
└── imdisk/
    ├── imdisk.exe   ImDisk CLI tool
    └── imdisk.sys   ImDisk kernel driver (auto-installed on first use)
```

---

## ImDisk Attribution

deFrost bundles [ImDisk Virtual Disk Driver](http://www.ltr-data.se/opencode.html/#ImDisk)
by Olof Lagerkvist, licensed under the GNU LGPL. The driver files (`imdisk.exe`,
`imdisk.sys`) are included unchanged. Source code is available at the link above.

---

## What deFrost Does NOT Do

deFrost is a targeted mitigation for the FROST SSD timing attack. It is not a
general privacy or security tool. It does not:

- Replace a VPN or encrypt your network traffic
- Block other browser fingerprinting methods (canvas, WebGL, font enumeration)
- Protect your browsing data at the network level
- Prevent malware that has already compromised your system
- Work on macOS or Linux (different RAM disk mechanisms — possible future work)

---

## Security Model

- The Flask server binds exclusively to `127.0.0.1`. It is not accessible from
  any other machine on your network under any circumstances.
- No telemetry, no analytics, no external network calls of any kind.
- config.json is stored next to the executable. It contains profile paths and
  session state. It does not contain passwords or browser data.
- The ImDisk driver is a kernel-level component. It is installed once and
  persists as a Windows service. This is unavoidable for RAM disk functionality.
  The driver is open source and widely used.

---

## Contributing

Issues and pull requests are welcome. Areas where help is most useful:

- Testing on Edge, DuckDuckGo, Brave, Vivaldi, and Opera
- UI improvements to the setup and dashboard pages
- PyInstaller build testing on clean Windows machines
- Icon and visual design
- macOS/Linux port (different RAM disk approach required)

---

## License

MIT License. See [LICENSE](LICENSE) for details.

The bundled ImDisk driver is licensed under the GNU LGPL.
See [assets/imdisk/](assets/imdisk/) for attribution.

---

## Acknowledgements

FROST was discovered and disclosed by researchers at Graz University of
Technology. Their work made this mitigation possible.
