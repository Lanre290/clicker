"""Foreground-window context via raw Win32 calls (ctypes) — no extra
dependency. Used two ways:
  1. Context-awareness: tell Gemini what app currently has focus.
  2. Sensitive-app guard: never offer to act while a password manager /
     banking-style window is focused, regardless of mode or prior grants.

Investigated and dropped: making the guide overlay click-through via
WS_EX_TRANSPARENT, to guarantee clicks always reach the app underneath
even where the overlay draws something (a bracket line, the instruction
card) — Tk's -transparentcolor alone only passes clicks through the
literal transparent-colored pixels, confirmed by direct testing. The
WS_EX_TRANSPARENT approach couldn't be verified: every automated click-
delivery test came back worse than the unmodified version, but Windows'
foreground-lock behavior made the test harness itself unreliable (a
background-spawned test window couldn't reliably win real z-order/focus
priority to begin with, confirmed via WindowFromPoint returning an
unrelated top-level window even after the test window was made topmost
and lifted). Rather than ship an unverified low-level style change for
something this central, this was reverted — validate click delivery
directly when actually running the app interactively instead.
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
