"""Entry point: wires the continuous see -> understand -> guide -> offer ->
act loop to a change-detection background thread and an on-screen HUD.
There is no console interaction here — goal entry, mode toggling, and stop
all happen through the overlay (spotlight box, HUD buttons, hotkeys).

Run with: python run.py
"""

import threading
import time

import keyboard

from . import config, sound
from .act import ActError, perform
from .changeloop import _diff_score, _thumbnail, watch_loop
from .clicktrigger import register as register_click_trigger
from .clicktrigger import register_action_check
from .overlay import Overlay
from .see import capture_active_screen
from .understand import UnderstandError, understand
from .voice import VoiceError, listen_once, speak
from .winctx import foreground_window_title, is_sensitive


class SessionState:
    def __init__(self):
        self.autonomous = False
        self.act_granted_session = False
        self.stop_event = threading.Event()  # cancels the in-flight cycle
        self.shutdown = threading.Event()    # ends the watch loop (app exit)
        self.busy = threading.Event()        # a cycle is currently running
        self.listening = threading.Event()   # a voice capture is in progress
        self.paused = False                  # True after Stop, until goal/force-check resumes it
        self.goal = None
        self.history = []
        self.done = False
        self.guide_until = 0.0          # wall-clock time our own overlay is still expected on screen
        self.last_shown_instruction = None  # (instruction, label) most recently displayed to the user
        self.last_shown_label = None
        self.last_analyzed_thumb = None  # downsampled frame Gemini last actually saw, for the unchanged-screen gate
        self.lock = threading.Lock()


def _mark_guide_shown(state: SessionState, seconds):
    """Records how long our own overlay is expected to still be visible,
    so watch_loop (and the click-driven check) don't mistake its own
    artwork — or its fade-out — for a real screen change.
    """
    state.guide_until = time.time() + seconds + (config.FADE_MS / 1000) * 2 + config.GUIDE_SETTLE_PADDING_SECONDS


def _mark_guide_dismissed(state: SessionState):
    """Called when the user closes a guide card early via its 'x' button.
    Shrinks the "our own overlay is still on screen" window down to just
    the fade-out + settle padding — instead of the full remaining display
    duration set by _mark_guide_shown — so watch_loop resumes noticing real
    changes almost immediately rather than sitting out a timer for artwork
    that's no longer there.
    """
    state.guide_until = time.time() + (config.FADE_MS / 1000) + config.GUIDE_SETTLE_PADDING_SECONDS


def run_cycle(state: SessionState, overlay: Overlay, image=None, monitor=None, speak_response=False, force=False):
    """speak_response: True only for the single cycle spawned right after a
    voice-set goal — reads the guide's voice_description aloud, so a voice
    prompt gets a voice answer. Screen-change/click/force-check triggers
    that follow it stay silent, same as before; this isn't a standing
    narration mode.

    force: skips the unchanged-screen gate below — used only by the
    explicit force-check hotkey, where "check now regardless" is the point.
    """
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

        # If the screen looks the same as the last frame Gemini actually
        # analyzed, the step it described can't be done yet — there's
        # nothing new to say. Skip the call rather than asking again and
        # risking a differently-worded-but-same-meaning instruction slipping
        # past the exact-text repeat check below and re-flashing the guide
        # for a step the user hasn't completed. Click/Enter/Tab (unlike the
        # pixel-diff watch loop) fire on *any* action, including ones that
        # don't visibly change anything, so this is the actual gate now.
        thumb = _thumbnail(image)
        if (
            not force and state.last_analyzed_thumb is not None
            and _diff_score(thumb, state.last_analyzed_thumb) < config.CHANGE_THRESHOLD
        ):
            print("[clicker] Screen unchanged since last check — nothing new to say.")
            overlay.update_hud(status="watching")
            return

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
        state.last_analyzed_thumb = thumb

        if state.stop_event.is_set():
            overlay.update_hud(status="watching")
            return

        instruction = data["instruction"]
        target = data["target"]
        visible = target.get("visible", True)
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
            overlay.show_guide(
                instruction, x, y, label, seconds=config.OVERLAY_DISPLAY_SECONDS, visible=visible,
                on_dismiss=lambda: _mark_guide_dismissed(state),
            )
            _mark_guide_shown(state, config.OVERLAY_DISPLAY_SECONDS)
            if speak_response:
                speak(data.get("voice_description") or instruction)
            return

        # Same instruction pointing at the same thing as what we last showed
        # means the step still isn't done yet — the screen hasn't moved on.
        # Re-flashing the identical guide (and re-playing its sound) on every
        # stray click/Enter/Tab would just be nagging; stay quiet and keep
        # watching instead of repeating ourselves.
        if instruction == state.last_shown_instruction and label == state.last_shown_label:
            print(f"[clicker] No progress since last step — not re-prompting: {instruction}")
            overlay.update_hud(status="watching")
            return

        if visible:
            print(f"[clicker] GUIDE: {instruction}  (pointing at: {label!r} @ {x},{y})"
                  + ("  [sensitive app — guide only]" if sensitive else ""))
        else:
            print(f"[clicker] GUIDE (target not visible): {instruction}")
        overlay.update_hud(status="guiding")
        overlay.show_guide(
            instruction, x, y, label, seconds=config.OVERLAY_DISPLAY_SECONDS, visible=visible,
            on_dismiss=lambda: _mark_guide_dismissed(state),
        )
        _mark_guide_shown(state, config.OVERLAY_DISPLAY_SECONDS)
        sound.guide_shown()
        state.last_shown_instruction = instruction
        state.last_shown_label = label
        if speak_response:
            speak(data.get("voice_description") or instruction)

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

    def set_goal(new_goal, speak_response=False):
        with state.lock:
            state.goal = new_goal
            state.history = []
            state.done = False
        state.paused = False
        state.last_shown_instruction = None
        state.last_shown_label = None
        state.last_analyzed_thumb = None
        print(f"[clicker] Goal set: {new_goal!r}")
        overlay.update_hud(goal=new_goal, status="thinking")
        threading.Thread(
            target=run_cycle, args=(state, overlay), kwargs={"speak_response": speak_response}, daemon=True,
        ).start()

    def on_edit_goal():
        overlay.show_spotlight(state.goal, set_goal)

    def on_voice_trigger():
        if state.listening.is_set():
            return
        state.listening.set()

        def worker():
            try:
                overlay.update_hud(status="listening")
                sound.listening_started()
                print("[clicker] Listening for your goal...")
                text = listen_once()
                sound.listening_stopped_ok()
                print(f"[clicker] Heard: {text!r}")
                set_goal(text, speak_response=True)
            except VoiceError as e:
                sound.listening_stopped_error()
                print(f"[clicker] Voice input failed: {e}")
                overlay.update_hud(status="watching" if state.goal else "paused")
            finally:
                state.listening.clear()

        threading.Thread(target=worker, daemon=True).start()

    def on_stop():
        state.stop_event.set()
        state.paused = True
        overlay.clear_all()
        overlay.update_hud(status="paused")
        print("[clicker] STOPPED. Overlay cleared, in-flight step cancelled. "
              "Continuous checking paused until you set/edit the goal or force-check.")

    def on_toggle_mode():
        state.autonomous = not state.autonomous
        mode = "Autonomous" if state.autonomous else "Guide"
        print(f"[clicker] Mode switched to {mode}.")
        overlay.update_hud(mode=mode)

    def on_trigger(image, monitor):
        threading.Thread(target=run_cycle, args=(state, overlay, image, monitor), daemon=True).start()

    def on_force_check():
        state.paused = False
        threading.Thread(target=run_cycle, args=(state, overlay), kwargs={"force": True}, daemon=True).start()

    def on_action_check():
        """A real click or Enter/Tab press anywhere is a strong 'the user
        probably just acted' signal — react to it directly instead of
        waiting for the next poll to notice a pixel diff. Debounced
        upstream (clicktrigger.register_action_check); still guarded here
        against firing while busy, paused, or done.

        No longer gated on state.guide_until: that field exists so the
        pixel-diff watch loop doesn't mistake our own overlay's artwork for
        a real screen change, but it used to also block *this*, event-driven
        path for as long as the guide card was on screen (up to ~6s) — so a
        click on the exact thing just pointed at was silently ignored, and
        the user had to click again once that window passed. Real clicks
        don't need that guard: on_immediate (see clicktrigger.py) already
        clears our overlay the instant a click/Enter/Tab happens, so there's
        nothing left for a delayed capture to confuse with real UI.

        Also no longer gated on a minimum-interval-since-last-cycle-*started*
        floor: that anchor sat before the Gemini round trip, so a quick
        response left almost none of the floor "used up" — a click made the
        moment the guide appeared (exactly the fast, click-through-the-steps
        flow this is for) would land inside the floor and just get dropped,
        with nothing to retry it later. state.busy already rules out actual
        overlapping Gemini calls, and the unchanged-screen gate in run_cycle
        already rules out redundant ones — a hard time floor on top of both
        was only ever costing responsiveness, not buying real protection.
        """
        with state.lock:
            goal = state.goal
        if not goal or state.done or state.paused or state.busy.is_set():
            return

        def start_cycle():
            # Re-check: the world may have moved on during the settle wait
            # below (Stop pressed, another cycle already started, goal
            # cleared) — same guards as above, just re-applied post-delay.
            with state.lock:
                current_goal = state.goal
            if not current_goal or state.done or state.paused or state.busy.is_set():
                return
            run_cycle(state, overlay)

        # Whatever the click/Enter/Tab did (open a menu, navigate a page)
        # usually isn't instantaneous — give it a beat to settle before
        # capturing, instead of screenshotting mid-transition.
        threading.Timer(config.ACTION_CHECK_SETTLE_SECONDS, start_cycle).start()

    keyboard.add_hotkey(config.HOTKEY_STOP, on_stop)
    keyboard.add_hotkey(config.HOTKEY_AUTONOMOUS_TOGGLE, on_toggle_mode)
    keyboard.add_hotkey(config.HOTKEY_SPOTLIGHT, on_edit_goal)
    keyboard.add_hotkey(config.HOTKEY_FORCE_CHECK, on_force_check)
    register_click_trigger(on_voice_trigger)
    register_action_check(on_action_check, on_immediate=overlay.dismiss_current_guide)

    print("=" * 64)
    print("clicker -- continuous screen guide")
    print(f"  set/edit goal          : {config.HOTKEY_SPOTLIGHT}  (or click the HUD)")
    print(f"  speak your goal        : {config.CLICK_TRIGGER_COUNT}x rapid click anywhere, or the HUD mic button")
    print(f"  force a check now      : {config.HOTKEY_FORCE_CHECK}")
    print(f"  STOP everything        : {config.HOTKEY_STOP}")
    print(f"  toggle Guide/Autonomous: {config.HOTKEY_AUTONOMOUS_TOGGLE}  (or click the HUD)")
    print("  next step is automatic : watches for screen changes, clicks, Enter/Tab,")
    print("                           and switching windows -- no hotkey needed")
    print("  (also: yank the mouse into a screen corner to abort any in-flight act)")
    print("=" * 64)

    overlay.ensure_hud(
        on_toggle_mode=on_toggle_mode, on_stop=on_stop, on_edit_goal=on_edit_goal,
        on_voice_trigger=on_voice_trigger,
    )
    overlay.show_spotlight(None, set_goal)  # ask for the goal right away, no console needed

    threading.Thread(target=watch_loop, args=(state, on_trigger), daemon=True).start()

    overlay.run()


if __name__ == "__main__":
    main()
