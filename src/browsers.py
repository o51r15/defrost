"""
browsers.py — deFrost browser detection and profile enumeration

Detects installed browsers by checking known profile paths on Windows.
Supports Chrome, Firefox, Edge, DuckDuckGo, Brave, Vivaldi, Opera.
Chromium-family browsers share a common profile structure.
Firefox uses profiles.ini for profile discovery.
"""

import os
import json
import configparser
import re
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BrowserProfile:
    id: str                  # Internal ID (e.g. 'Default', 'Profile 1', 'Profile0')
    display_name: str        # Human-readable name shown in UI
    path: str                # Absolute path to the profile directory
    size_mb: float           # Current size of the profile directory in MB


@dataclass
class DetectedBrowser:
    id: str                  # Internal key (e.g. 'chrome', 'firefox')
    display_name: str        # Human-readable name (e.g. 'Google Chrome')
    exe_path: Optional[str]  # Path to browser executable, if found
    profiles: List[BrowserProfile] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Browser definitions
# ---------------------------------------------------------------------------

def _expand(path: str) -> str:
    return os.path.normpath(os.path.expandvars(path))


BROWSER_DEFINITIONS = {
    'chrome': {
        'display_name': 'Google Chrome',
        'profile_root': _expand(r'%LOCALAPPDATA%\Google\Chrome\User Data'),
        'type': 'chromium',
        'exe_paths': [
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            _expand(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        ],
    },
    'firefox': {
        'display_name': 'Mozilla Firefox',
        'profile_root': _expand(r'%APPDATA%\Mozilla\Firefox'),
        'type': 'firefox',
        'exe_paths': [
            r'C:\Program Files\Mozilla Firefox\firefox.exe',
            r'C:\Program Files (x86)\Mozilla Firefox\firefox.exe',
        ],
    },
    'edge': {
        'display_name': 'Microsoft Edge',
        'profile_root': _expand(r'%LOCALAPPDATA%\Microsoft\Edge\User Data'),
        'type': 'chromium',
        'exe_paths': [
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        ],
    },
    'duckduckgo': {
        'display_name': 'DuckDuckGo',
        'profile_root': _expand(r'%LOCALAPPDATA%\DuckDuckGo\Browser\User Data'),
        'type': 'chromium',
        'exe_paths': [
            _expand(r'%LOCALAPPDATA%\DuckDuckGo\Browser\Application\duckduckgo.exe'),
        ],
    },
    'brave': {
        'display_name': 'Brave',
        'profile_root': _expand(r'%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data'),
        'type': 'chromium',
        'exe_paths': [
            r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe',
            _expand(r'%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe'),
        ],
    },
    'vivaldi': {
        'display_name': 'Vivaldi',
        'profile_root': _expand(r'%LOCALAPPDATA%\Vivaldi\User Data'),
        'type': 'chromium',
        'exe_paths': [
            _expand(r'%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe'),
        ],
    },
    'opera': {
        'display_name': 'Opera',
        'profile_root': _expand(r'%APPDATA%\Opera Software\Opera Stable'),
        'type': 'chromium',
        'exe_paths': [
            _expand(r'%LOCALAPPDATA%\Programs\Opera\opera.exe'),
        ],
    },
}


# ---------------------------------------------------------------------------
# Size helpers
# ---------------------------------------------------------------------------

# Cache directories excluded from size estimates and copies.
# Defined here so both the fast scan and accurate scan use the same list.
CACHE_DIRS_EXCLUDE = {
    'Cache', 'Code Cache', 'GPUCache', 'DawnCache', 'ShaderCache',
    'cache2', 'startupCache', 'OfflineCache',
}


def _get_directory_size_mb_fast(path: Path) -> float:
    """
    Quick size estimate using os.scandir for browser detection UI.
    Excludes known cache directories so the reported size matches what
    deFrost will actually copy to RAM — no inflated cache numbers.
    """
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.name in CACHE_DIRS_EXCLUDE:
                    continue
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += _get_directory_size_mb_fast(Path(entry.path)) * 1024 * 1024
            except (PermissionError, OSError):
                pass
    except (PermissionError, OSError):
        pass
    return total / (1024 * 1024)


def get_profile_size_mb_accurate(profile_path: str, exclude_dirs: list = None) -> float:
    """
    Accurate profile size via robocopy /L (list-only, no files written).
    Pass exclude_dirs to match the same exclusions used during the actual
    copy — no point measuring what we won't be copying.
    Falls back to os.walk if robocopy is unavailable.
    """
    cmd = ['robocopy', profile_path, 'NUL', '/E', '/L', '/NP', '/NFL', '/NDL']
    if exclude_dirs:
        cmd += ['/XD'] + exclude_dirs
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith('bytes'):
                parts = stripped.replace(':', ' ').split()
                if len(parts) >= 2:
                    try:
                        val = float(parts[1])
                        unit = parts[2].lower() if len(parts) > 2 else ''
                        if unit == 'g':
                            return val * 1024
                        elif unit == 'm':
                            return val
                        elif unit == 'k':
                            return val / 1024
                        else:
                            return val / (1024 * 1024)
                    except ValueError:
                        pass
    except Exception:
        pass

    # Fallback: os.walk single pass
    exclude_set = set(exclude_dirs or [])
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(profile_path):
            dirnames[:] = [d for d in dirnames if d not in exclude_set]
            for fname in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fname))
                except OSError:
                    pass
    except Exception:
        pass
    return total / (1024 * 1024)


# ---------------------------------------------------------------------------
# Profile detection — Chromium family
# ---------------------------------------------------------------------------

def _detect_chromium_profiles(profile_root: str) -> List[BrowserProfile]:
    profiles = []
    root = Path(profile_root)
    if not root.exists():
        return profiles

    candidates = ['Default'] + [f'Profile {i}' for i in range(1, 20)]

    for candidate in candidates:
        profile_path = root / candidate
        if not profile_path.is_dir():
            continue

        # Try to get the human-readable name from Preferences JSON
        display_name = candidate
        prefs_path = profile_path / 'Preferences'
        if prefs_path.exists():
            try:
                with open(prefs_path, 'r', encoding='utf-8') as f:
                    prefs = json.load(f)
                name = prefs.get('profile', {}).get('name', '').strip()
                if name:
                    display_name = name
            except (json.JSONDecodeError, IOError, UnicodeDecodeError):
                pass

        size_mb = _get_directory_size_mb_fast(profile_path)
        profiles.append(BrowserProfile(
            id=candidate,
            display_name=display_name,
            path=str(profile_path),
            size_mb=round(size_mb, 1),
        ))

    return profiles


# ---------------------------------------------------------------------------
# Profile detection — Firefox
# ---------------------------------------------------------------------------

def _detect_firefox_profiles(profile_root: str) -> List[BrowserProfile]:
    profiles = []
    root = Path(profile_root)
    ini_path = root / 'profiles.ini'

    if not ini_path.exists():
        return profiles

    cfg = configparser.ConfigParser()
    try:
        cfg.read(str(ini_path), encoding='utf-8')
    except configparser.Error:
        return profiles

    # Identify the default profile path from Install* sections
    default_relative = None
    for section in cfg.sections():
        if section.lower().startswith('install'):
            default_relative = cfg[section].get('Default', None)
            break

    for section in cfg.sections():
        if not section.lower().startswith('profile'):
            continue

        name = cfg[section].get('Name', section)
        is_relative = cfg[section].get('IsRelative', '1') == '1'
        path_val = cfg[section].get('Path', '').replace('/', os.sep)

        if not path_val:
            continue

        full_path = (root / path_val) if is_relative else Path(path_val)
        full_path = full_path.resolve()

        if not full_path.is_dir():
            continue

        is_default = (path_val.replace('/', os.sep) == (default_relative or '').replace('/', os.sep))
        display_name = f'{name} (default)' if is_default else name
        size_mb = _get_directory_size_mb_fast(full_path)

        profiles.append(BrowserProfile(
            id=section,
            display_name=display_name,
            path=str(full_path),
            size_mb=round(size_mb, 1),
        ))

    return profiles


# ---------------------------------------------------------------------------
# Executable detection
# ---------------------------------------------------------------------------

def _find_exe(exe_paths: list) -> Optional[str]:
    for path in exe_paths:
        if os.path.isfile(path):
            return path
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_browsers() -> List[DetectedBrowser]:
    """
    Scan for installed browsers and their profiles.
    Returns a list of DetectedBrowser objects for browsers that have
    a valid profile directory on this machine.
    """
    results = []

    for browser_id, defn in BROWSER_DEFINITIONS.items():
        profile_root = defn['profile_root']
        browser_type = defn['type']
        exe_path = _find_exe(defn['exe_paths'])

        if browser_type == 'chromium':
            profiles = _detect_chromium_profiles(profile_root)
        elif browser_type == 'firefox':
            profiles = _detect_firefox_profiles(profile_root)
        else:
            profiles = []

        if not profiles:
            continue  # Skip browsers with no usable profiles

        results.append(DetectedBrowser(
            id=browser_id,
            display_name=defn['display_name'],
            exe_path=exe_path,
            profiles=profiles,
        ))

    return results


def get_profile_size_mb(profile_path: str, exclude_dirs: list = None) -> float:
    """
    Return profile size in MB, excluding cache dirs by default.
    Uses accurate robocopy measurement — call at activation time only.
    """
    return round(get_profile_size_mb_accurate(profile_path, exclude_dirs), 1)


def browsers_to_dict(browsers: List[DetectedBrowser]) -> dict:
    """Serialize detected browsers to a JSON-safe dict for config storage."""
    result = {}
    for b in browsers:
        result[b.id] = {
            'display_name': b.display_name,
            'exe_path': b.exe_path,
            'profiles': [
                {
                    'id': p.id,
                    'display_name': p.display_name,
                    'path': p.path,
                    'size_mb': p.size_mb,
                }
                for p in b.profiles
            ],
        }
    return result
