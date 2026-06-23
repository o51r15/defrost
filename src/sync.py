"""
sync.py — deFrost delta sync engine

Monitors the RAM disk for new writes and flushes changed files back to
the original profile location on disk using Robocopy.

Flush is triggered by write volume (size threshold), not time.
Only changed or new files are written — never the full profile.
"""

import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import psutil

import config

# ---------------------------------------------------------------------------
# Shared sync state (read by Flask status route)
# ---------------------------------------------------------------------------

sync_state = {
    'in_progress': False,
    'last_sync_time': None,
    'last_delta_mb': 0.0,
    'sync_count': 0,
    'error': None,
}

_monitor_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


# ---------------------------------------------------------------------------
# Directory size helper
# ---------------------------------------------------------------------------

def _dir_size_mb(path: str) -> float:
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += _dir_size_mb(entry.path) * 1024 * 1024
            except (PermissionError, OSError):
                pass
    except (PermissionError, OSError):
        pass
    return total / (1024 * 1024)


def _ram_disk_free_mb(drive_letter: str) -> float:
    """Return free space on the RAM disk in MB."""
    try:
        usage = psutil.disk_usage(f'{drive_letter}:\\')
        return usage.free / (1024 * 1024)
    except Exception:
        return 0.0


def _ram_disk_used_mb(drive_letter: str) -> float:
    """Return used space on the RAM disk in MB."""
    try:
        usage = psutil.disk_usage(f'{drive_letter}:\\')
        return usage.used / (1024 * 1024)
    except Exception:
        return 0.0


def _ram_disk_total_mb(drive_letter: str) -> float:
    """Return total capacity of the RAM disk in MB."""
    try:
        usage = psutil.disk_usage(f'{drive_letter}:\\')
        return usage.total / (1024 * 1024)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Robocopy delta flush
# ---------------------------------------------------------------------------

def flush_to_disk(ram_profile_path: str, disk_profile_path: str) -> bool:
    """
    Sync changed and new files from the RAM disk profile back to the
    original on-disk profile using Robocopy.

    Cache directories are excluded — they are RAM-only during the session
    and do not need to be persisted back to disk.

    /MIR  — mirrors source to destination (handles deletions)
    /Z    — restartable mode (handles locked files more gracefully)
    /W:0  — zero wait between retries
    /R:1  — only retry once per file
    /XD   — exclude cache directories from sync
    /NP /NFL /NDL — quiet output
    """
    sync_state['in_progress'] = True
    sync_state['error'] = None

    # Mirror cache exclusions from core.py
    cache_dirs = [
        'Cache', 'Code Cache', 'GPUCache', 'DawnCache', 'ShaderCache',
        'cache2', 'startupCache', 'OfflineCache',
    ]

    try:
        cmd = [
            'robocopy',
            ram_profile_path,
            disk_profile_path,
            '/MIR',
            '/Z',
            '/W:0',
            '/R:1',
            '/NP',
            '/NFL',
            '/NDL',
            '/XD',
            *cache_dirs,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Robocopy exit codes 0-7 are success/informational.
        # 8+ indicate errors.
        if result.returncode >= 8:
            sync_state['error'] = f'Robocopy exited with code {result.returncode}'
            print(f'[sync] Robocopy error: {result.stderr}')
            return False

        now = datetime.now().isoformat()
        sync_state['last_sync_time'] = now
        sync_state['sync_count'] += 1
        config.record_sync(0)
        return True

    except subprocess.TimeoutExpired:
        sync_state['error'] = 'Sync timed out after 120 seconds'
        return False
    except Exception as e:
        sync_state['error'] = str(e)
        return False
    finally:
        sync_state['in_progress'] = False


# ---------------------------------------------------------------------------
# Background monitor thread
# ---------------------------------------------------------------------------

def _monitor_loop(
    ram_profile_path: str,
    disk_profile_path: str,
    ram_drive_letter: str,
    initial_profile_mb: float,
    buffer_threshold_mb: float,
    on_flush: Optional[Callable] = None,
) -> None:
    """
    Background thread that polls RAM disk usage every 30 seconds.
    Triggers a flush when:
      - Delta writes exceed buffer_threshold_mb, OR
      - Free space on RAM disk drops below 10% of total capacity
    """
    print(f'[sync] Monitor started. Threshold: {buffer_threshold_mb}MB delta')
    poll_interval = 30  # seconds

    while not _stop_event.is_set():
        time.sleep(poll_interval)

        if _stop_event.is_set():
            break

        try:
            used_mb = _ram_disk_used_mb(ram_drive_letter)
            total_mb = _ram_disk_total_mb(ram_drive_letter)
            free_mb = _ram_disk_free_mb(ram_drive_letter)
            delta_mb = max(0.0, used_mb - initial_profile_mb)

            sync_state['last_delta_mb'] = round(delta_mb, 1)

            free_pct = (free_mb / total_mb * 100) if total_mb > 0 else 100
            threshold_hit = delta_mb >= buffer_threshold_mb
            emergency = free_pct < 10

            if threshold_hit or emergency:
                reason = 'buffer threshold' if threshold_hit else 'low free space emergency'
                print(f'[sync] Triggering flush ({reason}, delta={delta_mb:.1f}MB)')
                success = flush_to_disk(ram_profile_path, disk_profile_path)
                if success and on_flush:
                    on_flush()

        except Exception as e:
            print(f'[sync] Monitor error: {e}')

    print('[sync] Monitor stopped.')


def start_monitor(
    ram_profile_path: str,
    disk_profile_path: str,
    ram_drive_letter: str,
    initial_profile_mb: float,
    buffer_threshold_mb: float,
    on_flush: Optional[Callable] = None,
) -> None:
    """Start the background sync monitor thread."""
    global _monitor_thread
    _stop_event.clear()
    _monitor_thread = threading.Thread(
        target=_monitor_loop,
        args=(
            ram_profile_path,
            disk_profile_path,
            ram_drive_letter,
            initial_profile_mb,
            buffer_threshold_mb,
            on_flush,
        ),
        daemon=True,
        name='deFrost-SyncMonitor',
    )
    _monitor_thread.start()


def stop_monitor() -> None:
    """Signal the monitor thread to stop and wait for it to exit."""
    _stop_event.set()
    if _monitor_thread and _monitor_thread.is_alive():
        _monitor_thread.join(timeout=10)


def get_ram_disk_stats(drive_letter: str) -> dict:
    """Return current RAM disk usage stats for the status UI."""
    try:
        total = _ram_disk_total_mb(drive_letter)
        used = _ram_disk_used_mb(drive_letter)
        free = _ram_disk_free_mb(drive_letter)
        return {
            'total_mb': round(total, 1),
            'used_mb': round(used, 1),
            'free_mb': round(free, 1),
            'pct_used': round((used / total * 100) if total > 0 else 0, 1),
        }
    except Exception:
        return {'total_mb': 0, 'used_mb': 0, 'free_mb': 0, 'pct_used': 0}
