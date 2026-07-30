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

OVERLAY_DISPLAY_SECONDS = 8
FADE_MS = 220               # duration of overlay fade-in/out animations
SNAP_MS = 260                # duration of the reticle's converge-on-target animation
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
MIN_TRIGGER_INTERVAL_SECONDS = 1.5  # floor between two Gemini calls

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
