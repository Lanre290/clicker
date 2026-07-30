"""Step 1: See — capture a screenshot of the active (primary) screen.

Multi-monitor handling is explicitly out of scope for week 1 (see BRIEF.md),
so we always grab the primary monitor as reported by mss.
"""

import mss
from PIL import Image


def capture_active_screen():
    """Return (PIL.Image, monitor_dict) for the primary monitor.

    monitor_dict has 'left'/'top'/'width'/'height' in absolute screen
    coordinates, needed to translate Gemini's screenshot-relative pixel
    coordinates back into absolute coordinates for the overlay and pyautogui.
    """
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # index 0 is the "all monitors" bbox
        shot = sct.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return image, monitor
