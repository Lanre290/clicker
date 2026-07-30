"""Foreground-window context via raw Win32 calls (ctypes) — no extra
dependency. Used two ways:
  1. Context-awareness: tell Gemini what app currently has focus.
  2. Sensitive-app guard: never offer to act while a password manager /
     banking-style window is focused, regardless of mode or prior grants.
"""

import ctypes

_user32 = ctypes.windll.user32


def foreground_window_title():
    """Best-effort title of the currently focused top-level window, or ''."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = _user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def is_sensitive(window_title, patterns):
    """Case-insensitive substring match against a denylist.

    This is a heuristic, not a security boundary — a window title is
    trivially spoofable. It exists to bias the app toward guide-only
    behavior around obviously sensitive tools, not to guarantee safety.
    """
    if not window_title:
        return False
    title_lower = window_title.lower()
    return any(pattern.lower() in title_lower for pattern in patterns)
