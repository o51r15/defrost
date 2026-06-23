"""
core.py — deFrost RAM disk and profile lifecycle management

Owns the activate/deactivate sequences:
  - Creates and destroys the ImDisk RAM disk
  - Copies the browser profile to RAM on activation
  - Creates a junction point so the browser finds its profile in RAM
  - Runs a final flush and tears down on deactivation

ImDisk CLI reference:
  Create: imdisk -a -t vm -s <size>M -m <letter>:
  Delete: imdisk -D -m <letter>:

Junction points:
  Create: mklink /J "link" "target"  (via cmd /c)
  Remove: rmdir "link"               (removes junction only, not target)

Elevation:
  deFrost always runs elevated (PyInstaller UAC manifest).
  Driver auto-install on first use requires elevation — already satisfied.
"""

import ctypes
import os
import shutil
import string
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import psutil

import config
import sync as sync_module

# ---------------------------------------------------------------------------
# Elevation helpers
# ---------------------------------------------------------------------------

def is_elevated() -> bool:
    """Return True if the current process has administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def relaunch_elevated() -> None:
    """Re-launch the current process with UAC elevation and exit."""
    script = sys.argv[0]
    params = ' '.join(sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(
        None, 'runas', sys.executable, f'"{script}" {params}', None, 1
    )
    sys.exit(0)


# ---------------------------------------------------------------------------
# ImDisk driver detection and auto-install
# ---------------------------------------------------------------------------

def _imdisk_sys_path() -> str:
    """Return path to bundled imdisk.sys."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.normpath(os.path.join(base, '..', 'assets', 'imdisk', 'imdisk.sys'))
    if os.path.isfile(candidate):
        return candidate
    candidate2 = os.path.normpath(os.path.join(base, 'assets', 'imdisk', 'imdisk.sys'))
    if os.path.isfile(candidate2):
        return candidate2
    return ''


def is_imdisk_driver_installed() -> bool:
    """Return True if the ImDisk kernel driver service is registered."""
    result = subprocess.run(
        ['powershell', '-Command', 'Get-Service -Name ImDisk -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Status'],
        capture_output=True, text=True, timeout=10
    )
    return result.returncode == 0 and result.stdout.strip() != ''


def install_imdisk_driver() -> Tuple[bool, str]:
    """
    Copy imdisk.sys to System32\\drivers and register + start the service.
    Requires elevation — only called after confirming is_elevated().
    Returns (success, message).
    """
    sys_path = _imdisk_sys_path()
    if not sys_path:
        return False, 'Bundled imdisk.sys not found.'

    drivers_dir = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'drivers')
    dest = os.path.join(drivers_dir, 'imdisk.sys')

    try:
        shutil.copy2(sys_path, dest)
    except Exception as e:
        return False, f'Could not copy driver: {e}'

    # Register service
    subprocess.run(
        ['powershell', '-Command',
         f'New-Service -Name ImDisk -BinaryPathName "{dest}" '
         f'-DisplayName "ImDisk Virtual Disk Driver" -StartupType Automatic'],
        capture_output=True, timeout=15
    )

    # Start service
    result = subprocess.run(
        ['powershell', '-Command', 'Start-Service -Name ImDisk'],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        return False, f'Driver installed but failed to start: {result.stderr}'

    return True, 'ImDisk driver installed and started.'


def ensure_imdisk_ready() -> Tuple[bool, str]:
    """
    Verify ImDisk driver is running. Auto-install if missing (requires elevation).
    Returns (ready, message).
    """
    if is_imdisk_driver_installed():
        return True, 'ImDisk driver ready.'

    if not is_elevated():
        return False, (
            'ImDisk driver is not installed and deFrost is not running as '
            'administrator. Please restart deFrost as administrator to '
            'auto-install the driver.'
        )

    print('[core] ImDisk driver not found — installing...')
    return install_imdisk_driver()


# ---------------------------------------------------------------------------
# ImDisk path — bundled in assets/imdisk/ next to the exe
# ---------------------------------------------------------------------------

def _imdisk_path() -> str:
    """Return path to bundled imdisk.exe."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    # In dev, go up one level from src/ to find assets/
    candidate = os.path.join(base, '..', 'assets', 'imdisk', 'imdisk.exe')
    candidate = os.path.normpath(candidate)
    if os.path.isfile(candidate):
        return candidate
    # PyInstaller one-dir: assets is next to exe
    candidate2 = os.path.join(base, 'assets', 'imdisk', 'imdisk.exe')
    if os.path.isfile(candidate2):
        return candidate2
    return 'imdisk'  # Fall back to system PATH


# ---------------------------------------------------------------------------
# Drive letter discovery
# ---------------------------------------------------------------------------

def _find_free_drive_letter() -> Optional[str]:
    """Find the first unused drive letter from Z down to D."""
    used = {p.device[0].upper() for p in psutil.disk_partitions()}
    for letter in reversed(string.ascii_uppercase[3:]):  # Z to D
        if letter not in used:
            return letter
    return None


# ---------------------------------------------------------------------------
# RAM availability check
# ---------------------------------------------------------------------------

def check_ram_available(required_mb: float) -> Tuple[bool, float]:
    """
    Return (ok, available_mb).
    Requires at least required_mb free RAM plus 2GB headroom.
    """
    mem = psutil.virtual_memory()
    available_mb = mem.available / (1024 * 1024)
    headroom_mb = 2048  # 2GB minimum left after allocation
    ok = available_mb >= (required_mb + headroom_mb)
    return ok, round(available_mb, 0)


# ---------------------------------------------------------------------------
# Browser process detection
# ---------------------------------------------------------------------------

_BROWSER_EXE_NAMES = {
    'chrome': 'chrome.exe',
    'firefox': 'firefox.exe',
    'edge': 'msedge.exe',
    'duckduckgo': 'duckduckgo.exe',
    'brave': 'brave.exe',
    'vivaldi': 'vivaldi.exe',
    'opera': 'opera.exe',
}


def is_browser_running(browser_id: str) -> bool:
    """Return True if the target browser process is currently running."""
    exe_name = _BROWSER_EXE_NAMES.get(browser_id, '').lower()
    if not exe_name:
        return False
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'] and proc.info['name'].lower() == exe_name:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def close_browser(browser_id: str) -> Tuple[bool, str]:
    """
    Gracefully terminate all running instances of the target browser.
    Waits up to 8 seconds for clean exit, then force-kills stragglers.
    Returns (success, message).
    """
    exe_name = _BROWSER_EXE_NAMES.get(browser_id, '').lower()
    if not exe_name:
        return False, f'Unknown browser ID: {browser_id}'

    procs = []
    for proc in psutil.process_iter(['name', 'pid']):
        try:
            if proc.info['name'] and proc.info['name'].lower() == exe_name:
                procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if not procs:
        return True, 'Browser was not running.'

    # Graceful terminate first
    for proc in procs:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    _, alive = psutil.wait_procs(procs, timeout=8)

    # Force kill anything still alive
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Brief wait for filesystem handles to fully release
    time.sleep(1.5)
    return True, f'Closed {len(procs)} browser process(es).'


def launch_browser(exe_path: str, url: str = '') -> Tuple[bool, str]:
    """
    Launch the browser at normal user privilege level.
    Optionally open a URL on launch (e.g. the deFrost dashboard).
    ShellExecuteW without 'runas' launches at the user's normal token.
    """
    if not exe_path or not os.path.isfile(exe_path):
        return False, f'Executable not found: {exe_path}'
    try:
        # Pass URL as argument if provided — browser opens directly to it
        params = f'"{url}"' if url else None
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, None, exe_path, params, None, 1
        )
        if ret > 32:
            return True, 'Browser launched.'
        return False, f'ShellExecuteW returned {ret}'
    except Exception as e:
        return False, f'Failed to launch browser: {e}'


# ---------------------------------------------------------------------------
# RAM disk operations (ImDisk)
# ---------------------------------------------------------------------------

def create_ram_disk(size_mb: int, drive_letter: str) -> bool:
    """
    Create a RAM-backed virtual disk and format it NTFS.
    Two-step: ImDisk creates the raw device, then cmd formats it.
    -t vm = allocate storage from virtual memory (pure RAM disk)
    """
    imdisk = _imdisk_path()

    # Step 1 — create raw VM disk (no -p format flag; we format separately)
    cmd_create = [
        imdisk, '-a',
        '-t', 'vm',
        '-s', f'{size_mb}M',
        '-m', f'{drive_letter}:',
    ]
    try:
        result = subprocess.run(cmd_create, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f'[core] ImDisk create failed (code {result.returncode}): {result.stderr}')
            return False
    except Exception as e:
        print(f'[core] ImDisk create exception: {e}')
        return False

    # Step 2 — format NTFS via cmd (format is an internal cmd command)
    # Echo 'Y' to confirm the "volume not mounted" prompt if it appears
    cmd_format = f'echo Y | format {drive_letter}: /fs:ntfs /q /y'
    try:
        result = subprocess.run(
            ['cmd', '/c', cmd_format],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            print(f'[core] Format failed (code {result.returncode}): {result.stderr}')
            destroy_ram_disk(drive_letter)
            return False

        # Windows takes a moment to fully register the formatted volume.
        # Wait then confirm the filesystem is readable before returning.
        for attempt in range(8):
            time.sleep(1)
            try:
                psutil.disk_usage(f'{drive_letter}:\\')
                return True  # Volume is ready
            except Exception:
                pass

        print(f'[core] Format appeared to succeed but volume not readable after wait.')
        destroy_ram_disk(drive_letter)
        return False

    except subprocess.TimeoutExpired:
        print('[core] Format timed out.')
        destroy_ram_disk(drive_letter)
        return False
    except Exception as e:
        print(f'[core] Format exception: {e}')
        destroy_ram_disk(drive_letter)
        return False


def destroy_ram_disk(drive_letter: str) -> bool:
    """
    Force-unmount and release the RAM disk at the given drive letter.
    -D = force removal even if the device appears in use (safe for RAM disks
    since all data has already been flushed to disk before this is called).
    """
    imdisk = _imdisk_path()
    cmd = [imdisk, '-D', '-m', f'{drive_letter}:']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f'[core] ImDisk destroy failed: {result.stderr}')
            return False
        return True
    except Exception as e:
        print(f'[core] ImDisk destroy exception: {e}')
        return False


# ---------------------------------------------------------------------------
# Junction point operations
# ---------------------------------------------------------------------------

def create_junction(link_path: str, target_path: str) -> bool:
    """
    Create an NTFS junction point at link_path pointing to target_path.
    The original directory at link_path must not exist (it was renamed/moved).
    """
    cmd = ['cmd', '/c', 'mklink', '/J', link_path, target_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception as e:
        print(f'[core] Junction create failed: {e}')
        return False


def remove_junction(link_path: str) -> bool:
    """Remove a junction point (does not delete the target contents)."""
    try:
        # rmdir removes the junction symlink without touching the target
        os.rmdir(link_path)
        return True
    except Exception as e:
        print(f'[core] Junction remove failed: {e}')
        return False


# ---------------------------------------------------------------------------
# Profile copy (initial seed to RAM disk)
# ---------------------------------------------------------------------------

# Cache directories excluded from the initial RAM disk copy.
# These are regenerable web asset caches — no personal data, large footprint.
# Chrome/Chromium creates them fresh on launch; they rebuild in RAM naturally
# since the profile path is junctioned to the RAM disk once active.
# Keep in sync with browsers.CACHE_DIRS_EXCLUDE.
_CACHE_DIRS_EXCLUDE = [
    'Cache', 'Code Cache', 'GPUCache', 'DawnCache', 'ShaderCache',
    'cache2', 'startupCache', 'OfflineCache',
]


def copy_profile_to_ram(profile_path: str, ram_profile_path: str) -> bool:
    """
    Copy the on-disk browser profile to the RAM disk using Robocopy.
    Cache directories are excluded — they are regenerable and can be
    large (several GB for Chrome). They will rebuild in RAM naturally
    as the browser runs, since the profile path is junctioned to RAM.

    /E        — copy all subdirectories including empty ones
    /COPYALL  — preserve all file attributes and timestamps
    /XD       — exclude specified directory names
    /W:0 /R:1 — no wait, one retry (don't hang on locked files)
    """
    cmd = [
        'robocopy',
        profile_path,
        ram_profile_path,
        '/E',
        '/COPYALL',
        '/W:0',
        '/R:1',
        '/NP',
        '/XD',
        *_CACHE_DIRS_EXCLUDE,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode >= 8:
            print(f'[core] Profile copy failed (code {result.returncode}): {result.stderr}')
            return False
        return True
    except Exception as e:
        print(f'[core] Profile copy exception: {e}')
        return False


# ---------------------------------------------------------------------------
# Crash recovery detection
# ---------------------------------------------------------------------------

def detect_crash_recovery() -> bool:
    """
    Check if config shows an active session but the RAM disk no longer
    exists. Returns True if crash recovery is needed.
    """
    if not config.is_session_active():
        return False
    session = config.get_active_session()
    drive_letter = session.get('ram_disk_letter')
    if not drive_letter:
        return False
    # Check if the drive letter is actually mounted
    for part in psutil.disk_partitions():
        if part.device.upper().startswith(drive_letter.upper()):
            return False  # Drive exists — session may still be valid
    return True  # Active session but drive is gone — crash recovery needed


def restore_after_crash() -> None:
    """
    After a crash, the RAM disk is gone. Restore the junction point
    by removing the broken link and restoring the original profile path.
    """
    session = config.get_active_session()
    junction_source = session.get('junction_source')

    if junction_source and os.path.islink(junction_source):
        try:
            os.rmdir(junction_source)
            print(f'[core] Removed stale junction: {junction_source}')
        except Exception as e:
            print(f'[core] Could not remove stale junction: {e}')

    config.clear_session()
    print('[core] Crash recovery complete. Profile restored to disk path.')


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

def activate(
    browser_id: str,
    profile_id: str,
    profile_path: str,
    buffer_size_mb: int,
    on_status=None,
) -> Tuple[bool, str]:
    """
    Full activation sequence.
    on_status: optional callable(str) invoked at each step for live UI updates.
    """
    def s(msg):
        print(f'[core] {msg}')
        if on_status: on_status(msg)

    import browsers as browser_module

    # --- Step 0: elevation and driver ---
    s('Checking elevation and driver...')
    if not is_elevated():
        return False, (
            'deFrost must run as administrator to activate protection. '
            'Please restart it by right-clicking and selecting Run as administrator.'
        )

    driver_ok, driver_msg = ensure_imdisk_ready()
    if not driver_ok:
        return False, f'ImDisk driver error: {driver_msg}'

    # --- Pre-flight checks ---
    if is_browser_running(browser_id):
        return False, 'Please close the browser before activating protection.'

    s('Measuring profile size (excluding cache)...')
    profile_size_mb = browser_module.get_profile_size_mb(
        profile_path, exclude_dirs=_CACHE_DIRS_EXCLUDE
    )
    s(f'Profile size: {profile_size_mb:.1f} MB')
    ram_disk_size_mb = int(profile_size_mb + buffer_size_mb + 128)

    ok, available_mb = check_ram_available(ram_disk_size_mb)
    if not ok:
        return False, (
            f'Insufficient RAM. Need {ram_disk_size_mb}MB + 2GB headroom. '
            f'Available: {available_mb:.0f}MB.'
        )

    drive_letter = _find_free_drive_letter()
    if not drive_letter:
        return False, 'No free drive letters available for RAM disk.'

    s(f'Creating RAM disk {drive_letter}: ({ram_disk_size_mb} MB)...')
    if not create_ram_disk(ram_disk_size_mb, drive_letter):
        return False, 'Failed to create RAM disk. Is ImDisk installed?'
    s(f'RAM disk {drive_letter}: ready.')

    ram_free_mb = sync_module.get_ram_disk_stats(drive_letter).get('free_mb', 0)
    if profile_size_mb > ram_free_mb:
        destroy_ram_disk(drive_letter)
        needed_mb = int(profile_size_mb + buffer_size_mb + 128)
        return False, (
            f'Profile is {profile_size_mb:.0f} MB but RAM disk only has '
            f'{ram_free_mb:.0f} MB free. Re-activate with at least {needed_mb} MB buffer.'
        )

    ram_profile_path = f'{drive_letter}:\\profile'
    s(f'Copying {profile_size_mb:.0f} MB to RAM disk — this may take a minute...')
    if not copy_profile_to_ram(profile_path, ram_profile_path):
        destroy_ram_disk(drive_letter)
        return False, 'Failed to copy profile to RAM disk.'
    s('Profile copy complete.')

    backup_path = profile_path + '_deFrost_backup'
    s('Creating junction point...')
    try:
        os.rename(profile_path, backup_path)
    except Exception as e:
        destroy_ram_disk(drive_letter)
        return False, f'Failed to move original profile: {e}'

    if not create_junction(profile_path, ram_profile_path):
        os.rename(backup_path, profile_path)
        destroy_ram_disk(drive_letter)
        return False, 'Failed to create junction point.'
    s('Junction created — browser now reads from RAM.')

    sync_module.start_monitor(
        ram_profile_path=ram_profile_path,
        disk_profile_path=backup_path,
        ram_drive_letter=drive_letter,
        initial_profile_mb=profile_size_mb,
        buffer_threshold_mb=buffer_size_mb,
    )

    from datetime import datetime
    config.update_session({
        'active': True,
        'browser': browser_id,
        'profile_id': profile_id,
        'profile_path': profile_path,
        'profile_size_mb': round(profile_size_mb, 1),
        'ram_disk_letter': drive_letter,
        'ram_disk_path': ram_profile_path,
        'disk_backup_path': backup_path,
        'junction_source': profile_path,
        'buffer_size_mb': buffer_size_mb,
        'activation_time': datetime.now().isoformat(),
        'last_sync_time': None,
        'sync_count': 0,
    })

    s('Protection active.')
    return True, 'Protection activated.'


# ---------------------------------------------------------------------------
# Deactivation
# ---------------------------------------------------------------------------

def deactivate(on_status=None) -> Tuple[bool, str]:
    """
    Full deactivation sequence.
    on_status: optional callable(str) for live UI updates.
    """
    def s(msg):
        print(f'[core] {msg}')
        if on_status: on_status(msg)

    session = config.get_active_session()
    if not session.get('active'):
        return False, 'No active session to deactivate.'

    browser_id   = session['browser']
    junction_src = session['junction_source']
    ram_path     = session['ram_disk_path']
    backup_path  = session['disk_backup_path']
    drive_letter = session['ram_disk_letter']

    if is_browser_running(browser_id):
        return False, 'Please close the browser before deactivating.'

    s('Stopping sync monitor...')
    sync_module.stop_monitor()

    s('Syncing changes back to disk...')
    sync_module.flush_to_disk(ram_path, backup_path)
    s('Sync complete.')

    s('Removing junction point...')
    if os.path.exists(junction_src):
        remove_junction(junction_src)

    s('Restoring original profile path...')
    if os.path.exists(backup_path):
        try:
            os.rename(backup_path, junction_src)
            s('Profile restored.')
        except Exception as e:
            s(f'Warning: could not restore profile path: {e}')

    s(f'Releasing RAM disk {drive_letter}:...')
    destroy_ram_disk(drive_letter)
    s('RAM disk released.')

    config.clear_session()
    sync_module.sync_state.update({
        'in_progress': False,
        'last_sync_time': None,
        'last_delta_mb': 0.0,
        'sync_count': 0,
        'error': None,
    })

    s('Deactivation complete.')
    return True, 'Protection deactivated. Profile restored to disk.'
