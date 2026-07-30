"""Global action-event triggers: things clicker listens for directly instead
of only inferring "the user did something" from polling the screen for
pixel changes. Two independent purposes:

  1. `register` — fires a callback on config.CLICK_TRIGGER_COUNT rapid
     left-clicks in a row, anywhere on screen. Used to summon voice input
     without needing to focus clicker's own window first.
  2. `register_action_check` — fires a debounced callback after any real
     click OR a commit-style key press (Enter/Tab), anywhere on screen.
     Lets the watch loop react to "the user just acted" immediately,
     instead of waiting for the next poll to notice a pixel diff.

The counting/debounce logic is plain functions of timestamps so they can be
unit-tested without real OS hooks — `register`/`register_action_check` are
the only parts that touch `mouse`/`keyboard` directly.
"""

import threading

import keyboard
import mouse

from . import config


def make_click_counter(on_trigger, count=None, window_ms=None):
    """Returns a callable to pass as the click-event handler. Kept separate
    from `register` so the counting logic can be driven directly in tests
    without installing a real global mouse hook.
    """
    count = count or config.CLICK_TRIGGER_COUNT
    window_s = (window_ms or config.CLICK_TRIGGER_WINDOW_MS) / 1000.0
    clicks = []

    def on_click(now):
        clicks.append(now)
        while clicks and now - clicks[0] > window_s:
            clicks.pop(0)
        if len(clicks) >= count:
            clicks.clear()
            on_trigger()

    return on_click


def register(on_trigger):
    """Installs the global left-click hook. Non-blocking — `mouse` runs its
    own listener thread internally, same model as the `keyboard` hotkeys.
    """
    import time

    on_click = make_click_counter(on_trigger)
    mouse.on_click(lambda: on_click(time.time()))


def _make_debouncer(on_event, debounce_seconds):
    """Returns a zero-arg callable that, each time it's invoked, restarts a
    timer; on_event() fires once debounce_seconds after the *last* call —
    so a burst of activity (a drag, rapid typing, the triple-click voice
    gesture) only fires on_event once, after things settle.
    """
    pending = {"timer": None}

    def fire():
        pending["timer"] = None
        on_event()

    def trigger():
        existing = pending["timer"]
        if existing is not None:
            existing.cancel()
        timer = threading.Timer(debounce_seconds, fire)
        timer.daemon = True
        pending["timer"] = timer
        timer.start()

    return trigger


def register_action_check(on_action_event, on_immediate=None, debounce_seconds=None):
    """Fires on_action_event() a short debounce period after any real click
    or Enter/Tab key press, anywhere on screen, regardless of focus — a
    separate hook from `register`'s multi-click voice counter (`mouse` and
    `keyboard` both support multiple independent handlers).

    Deliberately not *every* keystroke: hooking every key would fire while
    someone is just typing prose, spamming Gemini calls for a step that
    isn't done yet. Enter/Tab are the keys that usually mean "I just
    submitted/confirmed this" — a strong, low-noise "check now" signal.

    on_immediate, if given, runs on *every* click/Enter/Tab with no
    debounce at all — for reactions that should feel instant (e.g. clearing
    a now-stale guide card) even though the actual recapture-and-recheck
    behind on_action_event still waits for the debounce to settle.
    """
    debounce_seconds = debounce_seconds if debounce_seconds is not None else config.CLICK_CHECK_DEBOUNCE_SECONDS
    trigger = _make_debouncer(on_action_event, debounce_seconds)

    def handle():
        if on_immediate is not None:
            on_immediate()
        trigger()

    mouse.on_click(handle)
    keyboard.on_press_key("enter", lambda _e: handle())
    keyboard.on_press_key("tab", lambda _e: handle())
