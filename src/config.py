"""
config.py — deFrost configuration management

Reads and writes config.json in the same directory as the executable.
Portable — no registry, no AppData, no installer traces.
"""

import json
import os
from datetime import datetime
from typing import Any, Optional

# Config file lives next to the exe (or next to this file in dev)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config.json')
CONFIG_PATH = os.path.normpath(CONFIG_PATH)

DEFAULTS: dict[str, Any] = {
    'last_browser': None,
    'last_profile': None,
    'buffer_size_mb': 200,
    'flask_port': 7375,
    'first_run': True,
    'detected_browsers': {},
    'active_session': {
        'active': False,
        'browser': None,
        'browser_display': None,
        'profile_id': None,
        'profile_path': None,
        'profile_size_mb': 0,
        'ram_disk_letter': None,
        'ram_disk_path': None,
        'disk_backup_path': None,
        'junction_source': None,
        'buffer_size_mb': 200,
        'activation_time': None,
        'last_sync_time': None,
        'sync_count': 0,
    }
}


def load_config() -> dict:
    """Load config from disk, filling in any missing keys with defaults."""
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULTS.copy())
        return DEFAULTS.copy()

    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # Deep-merge defaults for any missing top-level keys
        for key, value in DEFAULTS.items():
            if key not in data:
                data[key] = value
        # Deep-merge active_session defaults
        for key, value in DEFAULTS['active_session'].items():
            if key not in data.get('active_session', {}):
                data.setdefault('active_session', {})[key] = value
        return data
    except (json.JSONDecodeError, IOError) as e:
        print(f'[config] Failed to load config: {e} — using defaults')
        return DEFAULTS.copy()


def save_config(cfg: dict) -> None:
    """Write config dict to disk."""
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, default=str)
    except IOError as e:
        print(f'[config] Failed to save config: {e}')


def update_session(updates: dict) -> dict:
    """Merge updates into active_session and persist."""
    cfg = load_config()
    cfg['active_session'].update(updates)
    save_config(cfg)
    return cfg


def set_value(key: str, value: Any) -> None:
    """Set a single top-level config value and persist."""
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)


def is_session_active() -> bool:
    """Return True if config shows an active protection session."""
    cfg = load_config()
    return cfg.get('active_session', {}).get('active', False)


def get_active_session() -> dict:
    """Return the active_session block from config."""
    return load_config().get('active_session', {})


def record_sync(delta_mb: float) -> None:
    """Update last sync time and increment sync counter."""
    cfg = load_config()
    session = cfg['active_session']
    session['last_sync_time'] = datetime.now().isoformat()
    session['sync_count'] = session.get('sync_count', 0) + 1
    save_config(cfg)


def clear_session() -> None:
    """Reset active_session to its default (inactive) state."""
    cfg = load_config()
    cfg['active_session'] = DEFAULTS['active_session'].copy()
    save_config(cfg)
