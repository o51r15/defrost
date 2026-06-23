"""
tray.py — deFrost main entry point

Starts Flask in a daemon thread, then runs pystray on the main thread.
pystray requires the main thread on Windows — this is non-negotiable.

deFrost requires administrator privileges to:
  - Create and format NTFS RAM disks (ImDisk)
  - Create NTFS junction points
If not elevated, the app re-launches itself with UAC prompt.

Icon states:
  Blue   = Protected (profile in RAM)
  Gray   = Unprotected
  Yellow = Syncing / transitioning
"""

import sys
import os
import ctypes
import threading
import webbrowser
import time

from PIL import Image, ImageDraw
import pystray

import config
import core
import sync as sync_module
from app import run_flask


# ---------------------------------------------------------------------------
# Elevation check and re-launch
# ---------------------------------------------------------------------------

def _is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _relaunch_as_admin() -> None:
    """Re-launch this process with UAC elevation and exit current instance."""
    script = os.path.abspath(sys.argv[0])
    params = ' '.join(sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(
        None, 'runas', sys.executable, f'"{script}" {params}', None, 1
    )
    sys.exit(0)


# ---------------------------------------------------------------------------
# Icon generation (Pillow — replaced with .ico files in production)
# ---------------------------------------------------------------------------

def _make_icon(color: str) -> Image.Image:
    """Generate a simple shield-shaped tray icon in the given color."""
    size = 64
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Shield polygon (simplified)
    points = [
        (size // 2, 4),         # top center
        (size - 4, 12),         # top right
        (size - 4, 38),         # mid right
        (size // 2, size - 4),  # bottom center
        (4, 38),                # mid left
        (4, 12),                # top left
    ]
    draw.polygon(points, fill=color, outline='white')
    return img


ICON_PROTECTED   = _make_icon('#2563eb')  # blue
ICON_UNPROTECTED = _make_icon('#6b7280')  # gray
ICON_SYNCING     = _make_icon('#d97706')  # amber

_tray_icon: pystray.Icon | None = None


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def _is_active() -> bool:
    return config.is_session_active()


def _status_title() -> str:
    if sync_module.sync_state.get('in_progress'):
        return 'deFrost — Syncing...'
    return 'deFrost — Protected' if _is_active() else 'deFrost — Unprotected'


def _current_icon() -> Image.Image:
    if sync_module.sync_state.get('in_progress'):
        return ICON_SYNCING
    return ICON_PROTECTED if _is_active() else ICON_UNPROTECTED


# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------

def _open_dashboard(icon, item):
    cfg = config.load_config()
    port = cfg.get('flask_port', 7375)
    webbrowser.open(f'http://127.0.0.1:{port}/')


def _sync_now(icon, item):
    session = config.get_active_session()
    if not session.get('active'):
        return
    if sync_module.sync_state.get('in_progress'):
        return
    icon.icon = ICON_SYNCING
    sync_module.flush_to_disk(
        session['ram_disk_path'],
        session['disk_backup_path'],
    )
    icon.icon = ICON_PROTECTED


def _deactivate(icon, item):
    ok, msg = core.deactivate()
    if ok:
        icon.icon = ICON_UNPROTECTED
        icon.title = 'deFrost — Unprotected'
    _rebuild_menu(icon)


def _exit_app(icon, item):
    """Graceful exit — deactivate if active, then stop."""
    if _is_active():
        icon.icon = ICON_SYNCING
        icon.title = 'deFrost — Syncing before exit...'
        core.deactivate()
    icon.stop()


# ---------------------------------------------------------------------------
# Dynamic menu builder
# ---------------------------------------------------------------------------

def _rebuild_menu(icon: pystray.Icon) -> None:
    active = _is_active()
    syncing = sync_module.sync_state.get('in_progress', False)

    icon.menu = pystray.Menu(
        pystray.MenuItem(_status_title(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Open Dashboard', _open_dashboard),
        pystray.MenuItem('Sync Now', _sync_now, enabled=active and not syncing),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Deactivate', _deactivate, enabled=active),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Exit', _exit_app),
    )
    icon.icon = _current_icon()
    icon.title = _status_title()


# ---------------------------------------------------------------------------
# Icon refresh loop (updates icon state while running)
# ---------------------------------------------------------------------------

def _refresh_loop(icon: pystray.Icon) -> None:
    """Periodically refresh the icon and title to reflect current state."""
    while True:
        time.sleep(5)
        try:
            icon.icon = _current_icon()
            icon.title = _status_title()
        except Exception:
            break


# ---------------------------------------------------------------------------
# Startup checks
# ---------------------------------------------------------------------------

def _handle_startup() -> None:
    """Run elevation check, crash recovery, and first-run logic on launch."""
    # Re-launch elevated if not already — UAC prompt appears, then restarts
    import core as core_module
    if not core_module.is_elevated():
        print('[tray] Not elevated — relaunching with admin rights...')
        core_module.relaunch_elevated()
        return  # relaunch_elevated calls sys.exit — this line won't be reached

    if core.detect_crash_recovery():
        print('[tray] Crash recovery detected — restoring profile path.')
        core.restore_after_crash()

    cfg = config.load_config()
    if cfg.get('first_run', True):
        # Open setup page automatically on first launch
        time.sleep(1.5)  # Give Flask a moment to start
        port = cfg.get('flask_port', 7375)
        webbrowser.open(f'http://127.0.0.1:{port}/setup')


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = config.load_config()
    port = cfg.get('flask_port', 7375)

    # Start Flask in a background daemon thread
    flask_thread = threading.Thread(
        target=run_flask,
        args=(port,),
        daemon=True,
        name='deFrost-Flask',
    )
    flask_thread.start()

    # Startup checks (crash recovery, first-run redirect)
    startup_thread = threading.Thread(target=_handle_startup, daemon=True)
    startup_thread.start()

    # Build tray icon
    icon = pystray.Icon(
        name='deFrost',
        icon=_current_icon(),
        title=_status_title(),
    )

    # Start icon refresh loop
    refresh_thread = threading.Thread(
        target=_refresh_loop,
        args=(icon,),
        daemon=True,
        name='deFrost-IconRefresh',
    )
    refresh_thread.start()

    _rebuild_menu(icon)

    # pystray must run on the main thread on Windows
    icon.run()


if __name__ == '__main__':
    main()
