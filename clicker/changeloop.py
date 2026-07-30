"""Continuous, context-aware trigger: polls the screen, only fires a new
guide cycle once the screen has changed meaningfully and settled — instead
of firing on every poll or requiring a manual hotkey per step.
"""

import time

from PIL import Image, ImageChops

from . import config
from .see import capture_active_screen
from .winctx import foreground_window_title

_THUMB_SIZE = (160, 90)


def _thumbnail(image):
    return image.convert("L").resize(_THUMB_SIZE)


def _diff_score(thumb_a, thumb_b):
    diff = ImageChops.difference(thumb_a, thumb_b)
    stats = diff.getdata()
    return sum(stats) / len(stats)


def watch_loop(state, on_trigger):
    """Runs until state.shutdown is set. Calls on_trigger(image, monitor)
    whenever the screen has changed and settled, OR the foreground window
    switched to a different one — respecting cooldowns either way.

    state needs: shutdown (Event), done (bool), busy (Event), paused (bool),
    guide_until (float, wall-clock time our own overlay is expected to still
    be on screen), goal (str, read under state.lock).
    """
    last_analyzed_thumb = None
    pending_thumb = None
    stable_count = 0
    last_trigger_time = 0.0
    last_window_title = None

    while not state.shutdown.is_set():
        time.sleep(config.LOOP_POLL_INTERVAL_SECONDS)

        with state.lock:
            goal = state.goal
        if (
            not goal or state.done or state.paused or state.busy.is_set()
            or time.time() < state.guide_until
        ):
            # Don't track diffs while paused/idle/mid-guide — otherwise the
            # screen may drift far from last_analyzed_thumb while we're not
            # looking (or our own cursor/card/dashed-line artwork is on
            # screen), causing a spurious "changed" trigger that just
            # repeats the same instruction the instant we resume.
            last_analyzed_thumb = None
            pending_thumb = None
            stable_count = 0
            last_window_title = None
            continue

        image, monitor = capture_active_screen()
        thumb = _thumbnail(image)

        # A different foreground window is a strong, near-instant "the user
        # just navigated somewhere" signal — much stronger than waiting for
        # a pixel diff to cross CHANGE_THRESHOLD, since switching apps can
        # sometimes look pixel-similar (e.g. two similar-looking windows).
        window_title = foreground_window_title()
        window_changed = last_window_title is not None and window_title != last_window_title
        last_window_title = window_title

        if last_analyzed_thumb is None:
            last_analyzed_thumb = thumb
            continue

        score = _diff_score(thumb, last_analyzed_thumb)
        if score < config.CHANGE_THRESHOLD and not window_changed:
            pending_thumb = None
            stable_count = 0
            continue

        if pending_thumb is not None and _diff_score(thumb, pending_thumb) < config.STABLE_THRESHOLD:
            stable_count += 1
        else:
            stable_count = 0
        pending_thumb = thumb

        if stable_count < config.STABLE_FRAMES_REQUIRED:
            continue  # still changing (scroll/animation in progress) — wait

        now = time.time()
        if now - last_trigger_time < config.MIN_TRIGGER_INTERVAL_SECONDS:
            continue

        last_trigger_time = now
        last_analyzed_thumb = thumb
        pending_thumb = None
        stable_count = 0
        on_trigger(image, monitor)


__all__ = ["watch_loop", "_diff_score", "_thumbnail"]
