# Changelog

All notable changes to deFrost will be documented here.

---

## [Unreleased]

- Multiple instance prevention (PID lock file)
- Startup auto-activation option
- Proper tray icon assets (.ico)
- Build testing on clean Windows machine
- Edge, DuckDuckGo, Brave, Vivaldi, Opera end-to-end testing

---

## [0.1.0-alpha] — 2026-06-23

### First working release

**Core:**
- RAM disk creation and NTFS formatting via ImDisk
- Browser profile copy to RAM with cache directory exclusion
- NTFS junction point redirect (original path → RAM disk)
- Clean deactivation: delta sync → remove junction → restore profile → release RAM
- ImDisk driver auto-install on first activation (no manual setup)
- Crash recovery detection on startup

**Browsers:**
- Google Chrome — tested
- Mozilla Firefox — tested
- Microsoft Edge — implemented, untested
- DuckDuckGo Browser — implemented, untested
- Brave — implemented, untested
- Vivaldi — implemented, untested
- Opera — implemented, untested

**UI:**
- Flask web dashboard on 127.0.0.1:7375
- Setup page with browser/profile selection and RAM estimate
- Confirm modal before browser close
- Native floating status window (survives browser termination)
- Live step-by-step progress log during activation and deactivation
- Browser auto-closes and relaunches to the dashboard on completion

**Technical:**
- Size-threshold write buffer (not timer-based) — default 200 MB
- Accurate profile sizing via Robocopy /L scan (cache-excluded)
- Always-elevated execution via UAC (--uac-admin PyInstaller flag)
- Browser relaunch at normal user privilege via ShellExecuteW
- Portable — no installer, config.json next to exe
