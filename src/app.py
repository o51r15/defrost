"""
app.py — deFrost Flask web UI

Serves the local dashboard and setup interface.
Binds exclusively to 127.0.0.1 — never accessible from the network.
Port: 7375 (configurable in config.json)
"""

import os
import sys
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for

import config
import core
import sync as sync_module
import browsers as browser_module


# ---------------------------------------------------------------------------
# Flask setup — find templates relative to this file (dev) or _MEIPASS (built)
# ---------------------------------------------------------------------------

def _resource_dir(subdir: str) -> str:
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, subdir)


app = Flask(
    __name__,
    template_folder=_resource_dir('templates'),
    static_folder=_resource_dir('static'),
)
app.secret_key = os.urandom(24)

# Cache detected browsers in memory after first scan
_detected_browsers: dict = {}


# ---------------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    """Status dashboard."""
    cfg = config.load_config()
    session = cfg.get('active_session', {})
    is_active = session.get('active', False)

    ram_stats = {}
    if is_active:
        drive = session.get('ram_disk_letter', '')
        ram_stats = sync_module.get_ram_disk_stats(drive)

    return render_template(
        'index.html',
        is_active=is_active,
        session=session,
        ram_stats=ram_stats,
        sync_state=sync_module.sync_state,
    )


@app.route('/setup')
def setup():
    """Browser and profile selection page."""
    global _detected_browsers
    cfg = config.load_config()

    if not _detected_browsers:
        browsers = browser_module.detect_browsers()
        _detected_browsers = browser_module.browsers_to_dict(browsers)
        config.set_value('detected_browsers', _detected_browsers)
        config.set_value('first_run', False)

    session = cfg.get('active_session', {})

    return render_template(
        'setup.html',
        browsers=_detected_browsers,
        current_browser=cfg.get('last_browser'),
        current_profile=cfg.get('last_profile'),
        buffer_size=cfg.get('buffer_size_mb', 200),
        is_active=session.get('active', False),
    )


# ---------------------------------------------------------------------------
# Routes — actions
# ---------------------------------------------------------------------------

import json
import queue
import threading

import status_window as sw

# Shared activation status — written by background thread, read by poller
_act_status = {
    'running': False,
    'messages': [],
    'result': None,   # set when complete: {ok, message, launched}
}
_act_lock = threading.Lock()


def _run_activation(browser_id, profile_id, profile_path, buffer_mb, exe_path):
    """Background thread: runs full activation and updates _act_status."""
    def push(msg):
        with _act_lock:
            _act_status['messages'].append(msg)
        sw.push(msg, 'bright' if any(w in msg.lower() for w in
                ['complete','ready','active','created','restored','launched']) else 'dim')
        # Advance step chips
        m = msg.lower()
        if 'closing' in m or 'closed' in m:
            sw.step('close', 'active')
        if 'measuring' in m or 'creating ram' in m or 'copying' in m or 'junction' in m:
            sw.step('close', 'done')
            sw.step('work', 'active')
        if 'launching' in m or 'protection active' in m:
            sw.step('work', 'done')
            sw.step('launch', 'active')

    with _act_lock:
        _act_status['running'] = True
        _act_status['messages'] = []
        _act_status['result'] = None

    sw.show('deFrost — Activating')
    sw.step('close', 'active')
    sw.step('work',  'pending')
    sw.step('launch','pending')

    try:
        push('Closing browser...')
        close_ok, close_msg = core.close_browser(browser_id)
        push(close_msg)
        if not close_ok:
            sw.step('close', 'fail')
            sw.title('deFrost — Failed')
            sw.status('Failed to close browser.')
            with _act_lock:
                _act_status['result'] = {'ok': False, 'message': close_msg, 'step': 'close'}
                _act_status['running'] = False
            return

        sw.step('close', 'done')
        ok, msg = core.activate(
            browser_id=browser_id,
            profile_id=profile_id,
            profile_path=profile_path,
            buffer_size_mb=buffer_mb,
            on_status=push,
        )
        if not ok:
            sw.step('work', 'fail')
            sw.title('deFrost — Failed')
            sw.status(msg)
            with _act_lock:
                _act_status['result'] = {'ok': False, 'message': msg, 'step': 'activate'}
                _act_status['running'] = False
            return

        sw.step('work', 'done')
        push('Launching browser...')
        sw.step('launch', 'active')

        import time; time.sleep(0.5)
        sw.close()  # Close window just before relaunch

        launch_ok, launch_msg = core.launch_browser(exe_path, url='http://127.0.0.1:7375/')
        push(launch_msg if launch_ok else '⚠️ Could not relaunch automatically — please open manually.')

        with _act_lock:
            _act_status['result'] = {
                'ok': True,
                'message': 'Protection active.',
                'launched': launch_ok,
                'step': 'done',
            }
    except Exception as e:
        sw.title('deFrost — Error')
        sw.status(str(e))
        sw.push(str(e), 'fail')
        with _act_lock:
            _act_status['result'] = {'ok': False, 'message': str(e), 'step': 'error'}
    finally:
        with _act_lock:
            _act_status['running'] = False


@app.route('/activate', methods=['POST'])
def activate():
    """Start activation in a background thread. Poll /api/activation-status for progress."""
    data = request.get_json()
    browser_id   = data.get('browser_id')
    profile_id   = data.get('profile_id')
    profile_path = data.get('profile_path')
    buffer_mb    = int(data.get('buffer_mb', 200))
    exe_path     = data.get('exe_path', '')

    if not all([browser_id, profile_id, profile_path]):
        return jsonify({'ok': False, 'message': 'Missing required fields.'})

    config.set_value('last_browser', browser_id)
    config.set_value('last_profile', profile_id)
    config.set_value('buffer_size_mb', buffer_mb)

    t = threading.Thread(
        target=_run_activation,
        args=(browser_id, profile_id, profile_path, buffer_mb, exe_path),
        daemon=True,
        name='deFrost-Activation',
    )
    t.start()
    return jsonify({'ok': True, 'message': 'Activation started.'})


@app.route('/api/activation-status')
def api_activation_status():
    """Poll this to get live activation progress."""
    with _act_lock:
        return jsonify({
            'running': _act_status['running'],
            'messages': list(_act_status['messages']),
            'result': _act_status['result'],
        })


_deact_status = {
    'running': False,
    'messages': [],
    'result': None,
}
_deact_lock = threading.Lock()


def _run_deactivation(browser_id, exe_path):
    """Background thread: close browser, deactivate, relaunch."""
    def push(msg):
        with _deact_lock:
            _deact_status['messages'].append(msg)
        sw.push(msg, 'bright' if any(w in msg.lower() for w in
                ['complete','done','restored','released','launched']) else 'dim')
        m = msg.lower()
        if 'closing' in m or 'closed' in m:
            sw.step('close', 'active')
        if 'sync' in m or 'restoring' in m or 'junction' in m or 'releasing' in m:
            sw.step('close', 'done')
            sw.step('work', 'active')
        if 'relaunch' in m or 'deactivation complete' in m:
            sw.step('work', 'done')
            sw.step('launch', 'active')

    with _deact_lock:
        _deact_status['running'] = True
        _deact_status['messages'] = []
        _deact_status['result'] = None

    sw.show('deFrost — Deactivating')
    sw.step('close', 'active')
    sw.step('work',  'pending')
    sw.step('launch','pending')

    try:
        push('Closing browser...')
        close_ok, close_msg = core.close_browser(browser_id)
        push(close_msg)
        if not close_ok:
            sw.step('close', 'fail')
            sw.title('deFrost — Failed')
            sw.status(close_msg)
            with _deact_lock:
                _deact_status['result'] = {'ok': False, 'message': close_msg}
                _deact_status['running'] = False
            return

        sw.step('close', 'done')
        ok, msg = core.deactivate(on_status=push)
        if not ok:
            sw.step('work', 'fail')
            sw.title('deFrost — Failed')
            sw.status(msg)
            with _deact_lock:
                _deact_status['result'] = {'ok': False, 'message': msg}
                _deact_status['running'] = False
            return

        sw.step('work', 'done')
        push('Relaunching browser...')
        sw.step('launch', 'active')

        import time; time.sleep(0.5)
        sw.close()  # Close window just before relaunch

        launch_ok, launch_msg = core.launch_browser(exe_path, url='http://127.0.0.1:7375/')
        push(launch_msg if launch_ok else '⚠️ Could not relaunch automatically.')

        with _deact_lock:
            _deact_status['result'] = {
                'ok': True,
                'message': 'Protection deactivated.',
                'launched': launch_ok,
            }
    except Exception as e:
        sw.title('deFrost — Error')
        sw.status(str(e))
        sw.push(str(e), 'fail')
        with _deact_lock:
            _deact_status['result'] = {'ok': False, 'message': str(e)}
    finally:
        with _deact_lock:
            _deact_status['running'] = False


@app.route('/deactivate', methods=['POST'])
def deactivate():
    """Start deactivation in a background thread."""
    session = config.get_active_session()
    browser_id = session.get('browser', '')
    browsers_data = _detected_browsers or {}
    exe_path = browsers_data.get(browser_id, {}).get('exe_path', '')

    t = threading.Thread(
        target=_run_deactivation,
        args=(browser_id, exe_path),
        daemon=True,
        name='deFrost-Deactivation',
    )
    t.start()
    return jsonify({'ok': True, 'message': 'Deactivation started.'})


@app.route('/api/deactivation-status')
def api_deactivation_status():
    """Poll this for live deactivation progress."""
    with _deact_lock:
        return jsonify({
            'running': _deact_status['running'],
            'messages': list(_deact_status['messages']),
            'result': _deact_status['result'],
        })


@app.route('/sync', methods=['POST'])
def manual_sync():
    """Trigger an immediate manual sync."""
    session = config.get_active_session()
    if not session.get('active'):
        return jsonify({'ok': False, 'message': 'No active session.'})

    if sync_module.sync_state.get('in_progress'):
        return jsonify({'ok': False, 'message': 'Sync already in progress.'})

    ok = sync_module.flush_to_disk(
        session['ram_disk_path'],
        session['disk_backup_path'],
    )
    return jsonify({'ok': ok, 'message': 'Sync complete.' if ok else 'Sync failed.'})


@app.route('/rescan', methods=['POST'])
def rescan_browsers():
    """Re-run browser detection and update the cache."""
    global _detected_browsers
    browsers = browser_module.detect_browsers()
    _detected_browsers = browser_module.browsers_to_dict(browsers)
    config.set_value('detected_browsers', _detected_browsers)
    return jsonify({'ok': True, 'browsers': _detected_browsers})


# ---------------------------------------------------------------------------
# Routes — API (polled by dashboard JS)
# ---------------------------------------------------------------------------

@app.route('/api/status')
def api_status():
    """JSON status for dashboard polling (every 5 seconds)."""
    cfg = config.load_config()
    session = cfg.get('active_session', {})
    is_active = session.get('active', False)

    ram_stats = {}
    if is_active:
        drive = session.get('ram_disk_letter', '')
        ram_stats = sync_module.get_ram_disk_stats(drive)

    return jsonify({
        'is_active': is_active,
        'session': session,
        'ram_stats': ram_stats,
        'sync_state': sync_module.sync_state,
        'timestamp': datetime.now().isoformat(),
    })


@app.route('/api/browsers')
def api_browsers():
    """JSON list of detected browsers and their profiles."""
    global _detected_browsers
    if not _detected_browsers:
        browsers = browser_module.detect_browsers()
        _detected_browsers = browser_module.browsers_to_dict(browsers)
    return jsonify(_detected_browsers)


@app.route('/api/profiles/<browser_id>')
def api_profiles(browser_id):
    """Return profiles for a specific browser (for dynamic dropdown)."""
    global _detected_browsers
    if not _detected_browsers:
        browsers = browser_module.detect_browsers()
        _detected_browsers = browser_module.browsers_to_dict(browsers)

    browser = _detected_browsers.get(browser_id)
    if not browser:
        return jsonify({'profiles': []})

    # Return cached sizes from detection — accurate size is measured at
    # activation time, not here. No slow robocopy scan in the UI path.
    return jsonify({'profiles': browser.get('profiles', [])})


# ---------------------------------------------------------------------------
# Run function (called from tray.py in a daemon thread)
# ---------------------------------------------------------------------------

def run_flask(port: int = 7375) -> None:
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)
