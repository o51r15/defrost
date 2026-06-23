# deFrost — Developer Log
**Project:** deFrost  
**Purpose:** Mitigate the FROST browser SSD timing exploit by relocating browser profiles to a RAM disk  
**Started:** 2026-06-13  
**Last Updated:** 2026-06-23  
**Stack:** Python 3.13, Flask 3.1, pystray 0.19, PyInstaller 6.x, ImDisk (CLI + driver), Robocopy  
**Distribution:** Portable — no install required (ImDisk driver auto-installs on first use)

---

## Background

FROST (Fingerprinting Remotely using OPFS-based SSD Timing) is a browser-based
side-channel attack disclosed by researchers at Graz University of Technology in
May 2026, scheduled for presentation at DIMVA in July 2026.

A malicious webpage uses the Origin Private File System (OPFS) browser API to
create a large file and perform continuous random reads against it, measuring
SSD access latency. When the victim's browser reads its profile from the same
SSD, contention causes measurable latency changes. A CNN classifier trained on
these timing traces can identify visited websites with ~88.95% accuracy and
open desktop applications with ~95.83% accuracy. It works cross-browser —
a malicious page in Chrome can fingerprint activity in Firefox because both
read from the same physical SSD.

No browser vendor has shipped a fix. Google does not classify fingerprinting as
a security vulnerability. Apple called it out of scope. Mozilla acknowledged it
but has not acted.

deFrost eliminates the attack surface by moving the browser profile to a RAM
disk. Browser reads come from RAM — no SSD involvement, no timing channel,
no fingerprinting signal.

---

## Architecture Overview

```
deFrost.exe (PyInstaller portable bundle)
│
├── src/
│   ├── tray.py       — pystray system tray icon, main entry point
│   ├── app.py        — Flask web UI (127.0.0.1:7375 only)
│   ├── core.py       — RAM disk lifecycle, profile operations, activation
│   ├── browsers.py   — Browser detection and profile enumeration
│   ├── sync.py       — Delta sync engine, background monitor thread
│   └── config.py     — JSON config read/write, session state
│
├── src/templates/
│   ├── base.html     — Navbar, Bootstrap 5 dark theme
│   ├── index.html    — Dashboard (status, RAM usage, deactivate)
│   └── setup.html    — Browser/profile selection, activate flow
│
├── src/static/
│   └── style.css     — Dark UI, status cards, step chips, toasts
│
└── assets/
    └── imdisk/
        ├── imdisk.exe  — ImDisk CLI (copied from System32 at build time)
        └── imdisk.sys  — ImDisk kernel driver (bundled for auto-install)
```

Flask serves the web UI. pystray manages the tray icon on the main thread
with Flask running as a daemon thread. core.py owns the full RAM disk
lifecycle. All inter-module communication is direct function calls with no
external network, telemetry, or registry writes.

---

## Current State (2026-06-23)

### ✅ Completed and Working

**Core engine:**
- RAM disk creation via ImDisk CLI (two-step: create raw VM disk, then NTFS format)
- Format timing issue resolved — retry loop waits for Windows to register
  the formatted volume before proceeding
- Profile copy to RAM via Robocopy with cache directory exclusions
- Junction point creation (NTFS, mklink /J) redirecting original profile
  path to RAM disk location
- Delta sync monitor (background thread, size-threshold flush, not timer-based)
- Clean deactivation: sync → remove junction → restore backup → destroy RAM disk
- Crash recovery detection on startup (stale session in config, no RAM disk)

**Browser support:**
- Chrome: full detection, Preferences JSON parsing for profile display names,
  cache exclusion (Cache, Code Cache, GPUCache, DawnCache, ShaderCache)
- Firefox: profiles.ini parsing, default profile identification, cache exclusion
  (cache2, startupCache, OfflineCache)
- Edge, DuckDuckGo, Brave, Vivaldi, Opera: detection implemented, untested

**Size measurement:**
- Fast scandir scan (cache-excluded) for browser detection UI — sub-second
- Accurate robocopy /L scan (cache-excluded) at activation time for RAM sizing
- Chrome with 3.5GB full profile → ~2.1GB after cache exclusion

**Cache exclusion rationale:**
Browser cache is entirely regenerable web asset data. Excluding it from the
initial copy dramatically reduces RAM requirements (Chrome: 3.5GB → 2.1GB).
Cache directories are NOT copied to RAM at activation but rebuild naturally
during the session because the profile path is junctioned to the RAM disk —
new cache writes go to RAM automatically, so full FROST protection is maintained
throughout the session. Cookies, history, passwords, extensions, and all
personally identifying data ARE copied. Cache exclusions apply to both the
initial copy (Robocopy /XD) and the delta sync flushes.

**Elevation and driver:**
- App detects if running as admin on startup; if not, relaunches via ShellExecuteW
  with 'runas' verb
- ImDisk driver presence check via Get-Service on activation
- Auto-install: copies bundled imdisk.sys to System32/drivers, registers and
  starts the service — requires elevation (already satisfied)
- Browser relaunch uses ShellExecuteW WITHOUT 'runas' to launch at normal user
  privilege level (browsers refuse to run elevated)

**Web UI (Flask):**
- Setup page: browser dropdown → profile dropdown (populated dynamically via
  /api/profiles/{id}) → profile size (cache-excluded) → buffer selector →
  RAM required breakdown → Activate button
- Dashboard: protection status, RAM disk usage bar, last sync time, Sync Now,
  Deactivate buttons
- All endpoints bind to 127.0.0.1 only

**Activation UX flow:**
1. User clicks Activate Protection
2. Confirm modal: "deFrost will close [browser], copy profile to RAM,
   then relaunch automatically. Unsaved work will be lost."
3. Cancel or Continue
4. Continue → POST /activate → background thread starts
5. Browser UI closes (Chrome tab goes away — expected behavior)
6. Activation runs: close browser → measure size → create RAM disk →
   copy profile → create junction → start sync monitor
7. Browser relaunches automatically opening http://127.0.0.1:7375/
8. Dashboard shows 🛡️ PROTECTED

**Deactivation UX flow (mirrors activation):**
1. User clicks Deactivate on dashboard
2. Confirm modal: explains close/sync/relaunch
3. Continue → POST /deactivate → background thread starts
4. Browser closes
5. Deactivation runs: stop monitor → final sync to disk → remove junction →
   restore backup → destroy RAM disk → clear session
6. Browser relaunches to http://127.0.0.1:7375/
7. Dashboard shows ⚪ UNPROTECTED

**Live progress:**
- Both activate and deactivate run in background threads
- on_status callback in core.activate() and core.deactivate() pushes
  step-by-step messages to a shared status dict
- Client polls /api/activation-status or /api/deactivation-status every 800ms
- NOTE: Because the UI tab closes when Chrome is terminated, the polling
  overlay is not visible during Chrome activation. The status is visible in
  the PowerShell window. Browser reopens directly to the result (dashboard).
  This is acceptable behavior — the dashboard IS the result screen.

**Config (config.json — portable, no registry):**
```json
{
  "last_browser": "chrome",
  "last_profile": "Default",
  "buffer_size_mb": 200,
  "flask_port": 7375,
  "first_run": false,
  "active_session": {
    "active": true/false,
    "browser": "chrome",
    "profile_id": "Default",
    "profile_path": "...",
    "profile_size_mb": 2146.5,
    "ram_disk_letter": "Y",
    "ram_disk_path": "Y:\\profile",
    "disk_backup_path": "...Default_deFrost_backup",
    "junction_source": "...",
    "buffer_size_mb": 200,
    "activation_time": "...",
    "last_sync_time": "...",
    "sync_count": 0
  }
}
```

---

## Known Issues and Open Items

### High Priority

**1. Polling overlay not visible during Chrome activation**
When Chrome is the target browser, the deFrost UI tab closes when Chrome is
terminated in step 1. The activation continues correctly in the background and
Chrome reopens on the dashboard. However the user has no visible feedback during
the copy phase (which can take 1-2 minutes for large profiles). The PowerShell
window shows progress. Potential fixes:
- Open the deFrost UI in a secondary small browser (e.g. Edge or Firefox)
  before closing the target browser
- Use a lightweight native window (tkinter or win10toast) for the progress UI
  that is independent of the browser being activated
- Accept current behavior as-is (dashboard-as-result is clean UX)

**2. Single browser/profile at a time (v1 limitation)**
Only one browser profile can be protected simultaneously. Multi-profile
or multi-browser support deferred to v2.

**3. Multiple instance prevention**
No mutex or lock file to prevent running deFrost twice. Two instances will
conflict badly if both try to activate. Fix: check for existing process or
write a PID file on startup.

### Medium Priority

**4. Buffer size in setup UI is misleading for large profiles**
"Recommended — 200MB" buffer next to "3738 MB RAM Required" confuses users
who expect RAM Required ≈ buffer. The RAM required breakdown now shows the
formula inline (profile + buffer + overhead) which helps, but the button
label could be clearer. Consider renaming to "Extra headroom" or similar.

**5. Edge profile display name**
Edge in default state shows "Profile 1" rather than a human-readable name
because the Preferences JSON uses a different key than Chrome. Low priority
since the user confirmed they don't use Edge.

**6. Firefox profile size shows 0.0 MB for the empty default profile**
The legacy `aadebb2w.default` profile shows 0.0 MB. This is correct
(empty profile) but looks odd. Could filter out empty/unused profiles.

### Low Priority

**7. Startup auto-activation**
Config option to auto-reactivate last session on launch not yet implemented.
Risky if something went wrong last session — keep off by default.

**8. Tray icon uses Pillow-generated placeholder shields**
Real .ico assets not yet created. Pillow shields are functional but rough.
Replace with proper icon set before release.

**9. Build not yet tested on a clean machine**
PyInstaller bundle (build.bat) not yet run. Functionality confirmed only in
dev/source mode. --uac-admin flag in build.bat should handle elevation
automatically in the compiled exe.

---

## Phase Completion Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Project scaffold | ✅ Complete |
| 2 | Browser detection | ✅ Complete |
| 3 | RAM disk management | ✅ Complete |
| 4 | Delta sync engine | ✅ Complete |
| 5 | Flask web UI | ✅ Complete (functional) |
| 6 | System tray | ✅ Complete (placeholder icons) |
| 7 | Config and state | ✅ Complete |
| 8 | PyInstaller packaging | 🔲 Not yet run |
| 9 | Testing | 🔄 In progress |
| 10 | Documentation and release | 🔲 Not started |

---

## Bug Fix Log

| Date | Bug | Fix |
|------|-----|-----|
| 2026-06-23 | Browser detection hanging (scandir spawning robocopy recursively) | Separated fast scandir scan (detection UI) from accurate robocopy scan (activation). Detection uses _get_directory_size_mb_fast, activation uses get_profile_size_mb_accurate with cache exclusions |
| 2026-06-23 | Chrome profile reported as 3501 MB including cache | Added CACHE_DIRS_EXCLUDE set to both fast and accurate scans. Chrome drops from 3.5GB to ~2.1GB |
| 2026-06-23 | RAM disk showed 0 MB free after format | Fixed missing colon in drive path strings in sync.py (_ram_disk_free_mb, _ram_disk_used_mb, _ram_disk_total_mb used f'{letter}\\' instead of f'{letter}:\\') |
| 2026-06-23 | Format failed with "Access Denied" | Windows format.com requires elevation. Fixed by running deFrost elevated and using cmd /c echo Y \| format with /q /y flags |
| 2026-06-23 | Format exit code 0 but volume unreadable | Added post-format retry loop (8 attempts × 1s) waiting for Windows to register the formatted NTFS volume before returning True |
| 2026-06-23 | Profile copy failed with "not enough space" | Robocopy was running before accurate size measurement. Fixed by measuring size (cache-excluded) before creating RAM disk so disk is sized correctly |
| 2026-06-23 | activate() had duplicate code blocks | Refactoring artifact from adding on_status callback. Removed orphaned old code block |
| 2026-06-23 | Browser relaunch failed from elevated process | Subprocess.Popen inherits elevated token; Chrome refuses to run as admin. Fixed by using ShellExecuteW(None, None, exe, ...) without 'runas' verb, which launches at normal user privilege |
| 2026-06-23 | Profile dropdown blank after browser selection | api_profiles route was calling get_profile_size_mb (slow robocopy scan) for each profile on every dropdown request. Fixed to return cached detection-time sizes instead |
| 2026-06-23 | Activation overlay disappears when Chrome closes | Chrome tab closes when Chrome is terminated as part of activation. Fixed by passing dashboard URL to launch_browser so Chrome reopens directly to http://127.0.0.1:7375/ showing the result |

---

## Key Design Decisions

**Size-threshold flush vs timer-based flush**
Write buffer triggers a delta sync when accumulated new writes reach the
threshold (default 200MB), not on a fixed timer. This avoids arbitrary
intervals, syncs proportionally to actual browsing activity, and produces
an irregular write pattern that is harder to characterize as a timing signal.

**Cache exclusion from copy and sync**
Browser cache is regenerable and can be gigabytes. Excluding it from the
initial copy (Robocopy /XD) and from delta syncs means the RAM disk is sized
for actual user data only. Cache rebuilds in RAM automatically because the
profile path is junctioned — new writes go to RAM regardless. Full FROST
protection is maintained because all browser reads during the session come
from RAM.

**Junction points over profile path changes**
Rather than modifying browser configuration to point at a different profile
path, deFrost creates an NTFS junction at the original profile location. The
browser never knows its profile moved — it opens the same path it always has,
which now transparently redirects to the RAM disk. No browser config changes,
no per-browser special cases.

**Always-elevated execution**
deFrost requires admin rights for: NTFS format, junction creation, ImDisk
driver installation. Running always-elevated (ShellExecuteW runas on startup,
--uac-admin in PyInstaller) is cleaner than requesting elevation piecemeal.
UAC prompt appears once per session. Browsers are relaunched at normal user
level via ShellExecuteW without runas verb.

**Portable no-install design**
config.json lives next to the exe. ImDisk driver is bundled and auto-installed
on first use. No registry writes, no AppData footprint, no installer. The
dist/deFrost folder can be copied anywhere and run. The ImDisk driver service
does persist in Windows (it is a kernel driver), but the deFrost application
files themselves are self-contained.

---

## Browser Expansion Roadmap

| Browser | Priority | Profile Structure | Status |
|---------|----------|-------------------|--------|
| Chrome | P0 | Chromium | ✅ Tested |
| Firefox | P0 | Firefox (profiles.ini) | ✅ Tested |
| Edge | P1 | Chromium | 🔲 Detection implemented, untested |
| DuckDuckGo | P1 | Chromium | 🔲 Detection implemented, untested |
| Brave | P2 | Chromium | 🔲 Detection implemented, untested |
| Vivaldi | P2 | Chromium | 🔲 Detection implemented, untested |
| Opera | P3 | Chromium | 🔲 Detection implemented, untested |

All Chromium-family browsers use the same User Data folder structure and will
follow the same activation path as Chrome with minimal additional work.

---

## Next Steps

1. **Resolve progress visibility during Chrome activation** — decide on
   native window vs accept current dashboard-as-result UX
2. **Multiple instance prevention** — PID file or mutex on startup  
3. **Run PyInstaller build** — confirm portable bundle works on clean machine
4. **Test remaining browsers** — Edge, DuckDuckGo, Brave  
5. **Replace placeholder tray icons** — commission or create proper .ico set
6. **Write README.md** — user-facing documentation
7. **GitHub repository setup** — open source release

---

*Log started 2026-06-13. Updated 2026-06-23 after first successful end-to-end
activation and deactivation of Chrome and Firefox profiles.*
