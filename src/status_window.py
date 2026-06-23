"""
status_window.py — deFrost floating status window

A small native always-on-top window that shows live activation/deactivation
progress. Appears when an operation starts, closes just before browser
relaunch. Survives browser termination since it's a separate Python window.
"""

import threading
import tkinter as tk
from tkinter import font as tkfont
from typing import Optional


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_window: Optional['StatusWindow'] = None
_lock = threading.Lock()


class StatusWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('deFrost')
        self.root.resizable(False, False)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.96)
        self.root.overrideredirect(False)  # Keep title bar for dragging

        # Size and position — bottom right corner
        w, h = 380, 280
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = sw - w - 24
        y = sh - h - 60  # above taskbar
        self.root.geometry(f'{w}x{h}+{x}+{y}')

        # Colors
        BG     = '#0f172a'
        BORDER = '#1e293b'
        DIM    = '#475569'
        BRIGHT = '#e2e8f0'
        GREEN  = '#34d399'
        BLUE   = '#60a5fa'

        self.root.configure(bg=BG)

        # Title bar area
        header = tk.Frame(self.root, bg='#1e293b', pady=8)
        header.pack(fill=tk.X)

        self._title_var = tk.StringVar(value='deFrost — Starting...')
        title_lbl = tk.Label(
            header, textvariable=self._title_var,
            bg='#1e293b', fg=BRIGHT,
            font=('Segoe UI', 10, 'bold'),
            padx=12,
        )
        title_lbl.pack(side=tk.LEFT)

        # Step indicators
        steps_frame = tk.Frame(self.root, bg=BG, pady=6, padx=12)
        steps_frame.pack(fill=tk.X)

        self._step_vars = {}
        self._step_labels = {}
        step_names = [('close', '① Close'), ('work', '② Working'), ('launch', '③ Relaunch')]
        for key, label in step_names:
            var = tk.StringVar(value=label)
            lbl = tk.Label(
                steps_frame, textvariable=var,
                bg='#1e293b', fg=DIM,
                font=('Segoe UI', 8),
                padx=8, pady=3,
                relief='flat',
            )
            lbl.pack(side=tk.LEFT, padx=3)
            self._step_vars[key] = var
            self._step_labels[key] = lbl

        # Scrolling log
        log_frame = tk.Frame(self.root, bg=BG, padx=10, pady=4)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self._log = tk.Text(
            log_frame,
            bg='#0a0f1a', fg='#64748b',
            font=('Consolas', 8),
            relief='flat',
            bd=0,
            wrap=tk.WORD,
            state=tk.DISABLED,
            cursor='arrow',
            highlightthickness=1,
            highlightbackground='#1e293b',
        )
        self._log.pack(fill=tk.BOTH, expand=True)
        self._log.tag_config('bright', foreground=BRIGHT)
        self._log.tag_config('green',  foreground=GREEN)
        self._log.tag_config('blue',   foreground=BLUE)
        self._log.tag_config('dim',    foreground='#475569')

        # Bottom status bar
        self._status_var = tk.StringVar(value='Initialising...')
        status_bar = tk.Label(
            self.root, textvariable=self._status_var,
            bg='#0a0f1a', fg=DIM,
            font=('Segoe UI', 8),
            anchor='w', padx=12, pady=4,
        )
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    # ------------------------------------------------------------------

    def set_title(self, text: str):
        self.root.after(0, lambda: self._title_var.set(text))

    def set_status(self, text: str):
        self.root.after(0, lambda: self._status_var.set(text))

    def set_step(self, key: str, state: str):
        """state: pending | active | done | fail"""
        colors = {
            'pending': ('#475569', '#1e293b'),
            'active':  ('#93c5fd', '#1e3a5f'),
            'done':    ('#6ee7b7', '#064e3b'),
            'fail':    ('#fca5a5', '#450a0a'),
        }
        fg, bg = colors.get(state, colors['pending'])
        lbl = self._step_labels.get(key)
        if lbl:
            self.root.after(0, lambda: lbl.configure(fg=fg, bg=bg))

    def append_log(self, msg: str, style: str = 'dim'):
        def _append():
            self._log.configure(state=tk.NORMAL)
            self._log.insert(tk.END, '› ' + msg + '\n', style)
            self._log.see(tk.END)
            self._log.configure(state=tk.DISABLED)
        self.root.after(0, _append)

    def close(self):
        self.root.after(0, self.root.destroy)

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Public API — called from app.py background threads
# ---------------------------------------------------------------------------

def show(title: str = 'deFrost — Activating'):
    """Open the status window. Safe to call from any thread."""
    global _window

    def _create():
        global _window
        with _lock:
            _window = StatusWindow()
        _window.set_title(title)
        _window.run()  # blocks until window closes
        with _lock:
            _window = None

    t = threading.Thread(target=_create, daemon=True, name='deFrost-StatusWindow')
    t.start()

    # Brief wait for window to initialise
    import time
    time.sleep(0.3)


def push(msg: str, style: str = 'dim'):
    """Append a log line. Safe to call from any thread."""
    with _lock:
        w = _window
    if w:
        w.append_log(msg, style)


def step(key: str, state: str):
    """Update a step chip state. key: close|work|launch  state: pending|active|done|fail"""
    with _lock:
        w = _window
    if w:
        w.set_step(key, state)


def title(text: str):
    with _lock:
        w = _window
    if w:
        w.set_title(text)


def status(text: str):
    with _lock:
        w = _window
    if w:
        w.set_status(text)


def close():
    """Close the status window."""
    with _lock:
        w = _window
    if w:
        w.close()
