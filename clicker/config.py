import os

from dotenv import load_dotenv

load_dotenv()  # reads a .env file in the working directory, like Node's dotenv

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Global hotkeys (require the `keyboard` library; on Windows this usually
# needs the terminal running as Administrator to see key events system-wide).
HOTKEY_STOP = "ctrl+alt+q"               # panic button: halt everything now
HOTKEY_AUTONOMOUS_TOGGLE = "ctrl+alt+a"  # flip Guide <-> Autonomous mode
HOTKEY_SPOTLIGHT = "ctrl+alt+space"      # open the goal entry box
HOTKEY_FORCE_CHECK = "ctrl+alt+g"        # skip the change-detection wait, check now

OVERLAY_DISPLAY_SECONDS = 5  # now that the guide card has its own 'x', it doesn't need to linger as long
FADE_MS = 220               # duration of overlay fade-in/out animations
SNAP_MS = 650                # duration of the ghost cursor's glide-to-target animation
PULSE_PERIOD_MS = 1600      # duration of one breathing cycle on the guide reticle
SPIN_PERIOD_MS = 900        # duration of one rotation of the HUD "thinking" spinner

# -- continuous change-detection loop ---------------------------------------
# The background loop polls the screen, downsamples it, and compares it to
# the last frame that was actually analyzed. A new Gemini call only fires
# once the screen has changed enough AND held still for a few frames (so we
# don't fire mid-scroll or mid-animation), and not more often than the
# cooldown allows. Tuned for responsiveness: most of the perceived lag is the
# Gemini round trip (see IMAGE_MAX_DIMENSION / THINKING_LEVEL below), so the
# polling side stays fast and cheap (it's just an in-memory image diff).
LOOP_POLL_INTERVAL_SECONDS = 0.3
CHANGE_THRESHOLD = 10.0          # mean grayscale delta (0-255) to count as "changed"
STABLE_THRESHOLD = 4.0           # frame-to-frame delta below this counts as "settled"
STABLE_FRAMES_REQUIRED = 2       # consecutive settled polls before triggering
MIN_TRIGGER_INTERVAL_SECONDS = 1.5  # floor between two watch_loop-triggered Gemini calls (only; see main.py's
                                     # on_action_check for why the click-driven path doesn't use this floor)

# Extra pause after a guide is shown, on top of how long the overlay itself
# is on screen + its fade in/out. Without this, the watch loop can resume
# diffing while our own cursor/card/dashed-line artwork is still visible or
# mid-fade, mistake *that* for a real screen change, and re-trigger a check
# that just repeats the same instruction. Also gives a real UI action (e.g.
# a menu opening) a beat to actually finish before we look again.
GUIDE_SETTLE_PADDING_SECONDS = 0.6

# -- Gemini call speed ---------------------------------------------------------
# Screenshots are downscaled before upload: Gemini tiles large images into
# more patches (more tokens, more latency) than the coordinate accuracy
# needs here. thinking_level="low" trims ~1-1.5s per call in testing, which
# matters a lot for something meant to feel like it's watching live.
IMAGE_MAX_DIMENSION = 1280
THINKING_LEVEL = "low"

# -- sensitive-app guard ------------------------------------------------------
# Substrings matched case-insensitively against the focused window's title.
# Heuristic only (see winctx.is_sensitive) — forces guide-only, ignoring
# Autonomous mode and any "this session" grant.
SENSITIVE_APP_PATTERNS = [
    "1password", "bitwarden", "keepass", "lastpass", "dashlane",
    "windows security", "credential manager", "windows hello",
    "chase", "bank of america", "wells fargo", "paypal", "venmo",
    "coinbase", "metamask", "kraken", "binance",
]

# -- voice input ---------------------------------------------------------------
# Transcription uses a dedicated free STT engine (SpeechRecognition's default
# Google Web Speech recognizer), not Gemini — keeps voice capture off the
# Gemini token/quota budget entirely. "Auto-stop on silence" is
# SpeechRecognition's own pause_threshold behavior, not something we hand-roll.
VOICE_PAUSE_THRESHOLD_SECONDS = 0.8   # silence duration that ends a phrase
VOICE_LISTEN_TIMEOUT_SECONDS = 6      # give up if the user never starts talking
VOICE_PHRASE_TIME_LIMIT_SECONDS = 15  # hard cap on a single recording

# -- global multi-click voice trigger -------------------------------------------
# Deliberately triple-click (not double): double-clicks happen constantly in
# normal computer use (opening files, selecting words) and would misfire
# voice capture; three rapid clicks anywhere are rare enough to be a safe,
# unambiguous "start listening" gesture. Active globally, independent of
# window focus, for as long as clicker is running.
CLICK_TRIGGER_COUNT = 3
CLICK_TRIGGER_WINDOW_MS = 600  # max gap between consecutive clicks to still count

# -- event-driven check on real clicks -------------------------------------------
# A real click anywhere is a strong "the user probably just acted" signal —
# reacting to it directly feels far more responsive than waiting for the
# next poll to notice a diff. Debounced so a burst of clicks (a drag, a
# double-click, the triple-click voice gesture itself) only fires one check,
# after things settle for a moment. Kept short — this sits directly in the
# "click to next guide" latency path, on top of ACTION_CHECK_SETTLE_SECONDS
# below and the Gemini round trip itself.
CLICK_CHECK_DEBOUNCE_SECONDS = 0.35

# Extra wait after that debounce fires, before actually capturing the screen.
# The click itself is instantaneous, but whatever it opened (a menu, a
# dialog, a page navigation) usually isn't — capturing immediately risks
# grabbing a mid-transition frame and guiding off of it. Unlike the pixel-
# diff trigger in changeloop.py, this path has no settling check of its
# own, so it needs this fixed pause instead.
ACTION_CHECK_SETTLE_SECONDS = 0.4
