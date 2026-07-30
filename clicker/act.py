"""Step 6: Act — perform the granted click/type. Only ever called after
explicit permission (once/session) or Autonomous mode is on.
"""

import time

import pyautogui

# Built-in panic button: slamming the mouse into a screen corner raises
# pyautogui.FailSafeException and aborts whatever pyautogui call is in
# flight. Kept on top of our own stop hotkey, not instead of it.
pyautogui.FAILSAFE = True


class ActError(Exception):
    pass


def perform(action, x, y, stop_event=None):
    """Move to (x, y) and click, or click-then-type, depending on action['type'].

    stop_event is checked between each sub-step so the global stop hotkey can
    cut this off promptly; it can't interrupt a pyautogui call already in
    flight (use the screen-corner failsafe for that).
    """
    action_type = action.get("type")
    value = action.get("value", "")

    if action_type not in ("click", "type"):
        raise ActError(f"Unknown action type: {action_type!r}")

    if stop_event is not None and stop_event.is_set():
        return

    pyautogui.moveTo(x, y, duration=0.3)

    if stop_event is not None and stop_event.is_set():
        return

    pyautogui.click()

    if action_type == "type":
        if stop_event is not None and stop_event.is_set():
            return
        time.sleep(0.1)
        pyautogui.typewrite(value, interval=0.02)
