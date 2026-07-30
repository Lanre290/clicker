"""Entry point: wires the continuous see -> understand -> guide -> offer ->
act loop to a change-detection background thread and an on-screen HUD.
There is no console interaction here — goal entry, mode toggling, and stop
all happen through the overlay (spotlight box, HUD buttons, hotkeys).

Run with: python run.py
"""

import threading

import keyboard

from . import config
from .act import ActError, perform
from .changeloop import watch_loop
from .overlay import Overlay
from .see import capture_active_screen
from .understand import UnderstandError, understand
from .winctx import foreground_window_title, is_sensitive


class SessionState:
    def __init__(self):
        self.autonomous = False
        self.act_granted_session = False
        self.stop_event = threading.Event()  # cancels the in-flight cycle
        self.shutdown = threading.Event()    # ends the watch loop (app exit)
        self.busy = threading.Event()        # a cycle is currently running
        self.goal = None
        self.history = []
        self.done = False
        self.lock = threading.Lock()


def run_cycle(state: SessionState, overlay: Overlay, image=None, monitor=None):
    if state.busy.is_set():
        return
    state.busy.set()
    state.stop_event.clear()
    try:
        with state.lock:
            goal = state.goal
        if not goal or state.done:
            return

        if image is None or monitor is None:
            image, monitor = capture_active_screen()

        window_title = foreground_window_title()
        sensitive = is_sensitive(window_title, config.SENSITIVE_APP_PATTERNS)

        overlay.update_hud(status="thinking")
        try:
            data = understand(
                image, goal, history=state.history,
                window_title=window_title, sensitive=sensitive,
            )
        except UnderstandError as e:
            print(f"[clicker] understand() failed: {e}")
            overlay.update_hud(status="watching")
            return

        if state.stop_event.is_set():
            overlay.update_hud(status="watching")
            return

        instruction = data["instruction"]
        target = data["target"]
        x = monitor["left"] + int(target["x"])
        y = monitor["top"] + int(target["y"])
        label = target.get("label", "")

        state.history.append(instruction)
        if len(state.history) > 12:
            state.history = state.history[-12:]

        if data.get("goal_complete"):
            state.done = True
            print(f"[clicker] GOAL COMPLETE: {instruction}")
            overlay.update_hud(status="done")
            overlay.show_guide(instruction, x, y, label, seconds=config.OVERLAY_DISPLAY_SECONDS)
            return

        print(f"[clicker] GUIDE: {instruction}  (pointing at: {label!r} @ {x},{y})"
              + ("  [sensitive app — guide only]" if sensitive else ""))
        overlay.update_hud(status="guiding")
        overlay.show_guide(instruction, x, y, label, seconds=config.OVERLAY_DISPLAY_SECONDS)

        if state.stop_event.is_set() or not data.get("offer_to_act"):
            overlay.update_hud(status="watching")
            return

        can_act = state.autonomous or state.act_granted_session
        if not can_act:
            reason = data.get("reason", "")
            print(f"[clicker] OFFER TO ACT: {reason}")
            scope = overlay.ask_permission_sync(reason)
            print(f"[clicker] User granted: {scope}")
            if scope == "no":
                overlay.update_hud(status="watching")
                return
            if scope == "session":
                state.act_granted_session = True

        if state.stop_event.is_set():
            overlay.update_hud(status="watching")
            return

        action = data.get("action", {})
        print(f"[clicker] ACT: {action}")
        try:
            perform(action, x, y, stop_event=state.stop_event)
        except ActError as e:
            print(f"[clicker] act() failed: {e}")

        overlay.update_hud(status="watching")
    finally:
        state.busy.clear()


def main():
    state = SessionState()
    overlay = Overlay()

    def set_goal(new_goal):
        with state.lock:
            state.goal = new_goal
            state.history = []
            state.done = False
        print(f"[clicker] Goal set: {new_goal!r}")
        overlay.update_hud(goal=new_goal, status="watching")

    def on_edit_goal():
        overlay.show_spotlight(state.goal, set_goal)

    def on_stop():
        state.stop_event.set()
        overlay.clear_all()
        overlay.update_hud(status="paused")
        print("[clicker] STOPPED. Overlay cleared, in-flight step cancelled.")

    def on_toggle_mode():
        state.autonomous = not state.autonomous
        mode = "Autonomous" if state.autonomous else "Guide"
        print(f"[clicker] Mode switched to {mode}.")
        overlay.update_hud(mode=mode)

    def on_trigger(image, monitor):
        threading.Thread(target=run_cycle, args=(state, overlay, image, monitor), daemon=True).start()

    def on_force_check():
        threading.Thread(target=run_cycle, args=(state, overlay), daemon=True).start()

    keyboard.add_hotkey(config.HOTKEY_STOP, on_stop)
    keyboard.add_hotkey(config.HOTKEY_AUTONOMOUS_TOGGLE, on_toggle_mode)
    keyboard.add_hotkey(config.HOTKEY_SPOTLIGHT, on_edit_goal)
    keyboard.add_hotkey(config.HOTKEY_FORCE_CHECK, on_force_check)

    print("=" * 64)
    print("clicker -- continuous screen guide")
    print(f"  set/edit goal          : {config.HOTKEY_SPOTLIGHT}  (or click the HUD)")
    print(f"  force a check now      : {config.HOTKEY_FORCE_CHECK}")
    print(f"  STOP everything        : {config.HOTKEY_STOP}")
    print(f"  toggle Guide/Autonomous: {config.HOTKEY_AUTONOMOUS_TOGGLE}  (or click the HUD)")
    print("  (also: yank the mouse into a screen corner to abort any in-flight act)")
    print("=" * 64)

    overlay.ensure_hud(on_toggle_mode=on_toggle_mode, on_stop=on_stop, on_edit_goal=on_edit_goal)
    overlay.show_spotlight(None, set_goal)  # ask for the goal right away, no console needed

    threading.Thread(target=watch_loop, args=(state, on_trigger), daemon=True).start()

    overlay.run()


if __name__ == "__main__":
    main()
