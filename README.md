# clicker

A screen-aware guide: watches your screen continuously, points at the next
thing to do, and only touches the mouse/keyboard when you say yes. Fully
overlay-driven — no console interaction. See [BRIEF.md](BRIEF.md) for the
original design brief.

## Setup

```powershell
cd c:\clicker
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Set your Gemini API key by copying `.env.example` to `.env` and filling it in:

```powershell
copy .env.example .env
notepad .env
```

`.env` is gitignored, so your key never gets committed. `config.py` loads it
automatically on startup via `python-dotenv` — no shell env vars needed.

(If you'd rather not use a `.env` file, `$env:GEMINI_API_KEY = "your-key-here"`
in the current PowerShell session works too and takes priority.)

## Run

```powershell
python run.py
```

**Run the terminal as Administrator.** The `keyboard` library needs elevated
privileges on Windows to see global hotkeys system-wide (i.e. while some
other app has focus, which is the whole point).

## How it works

Everything happens through the overlay — there's no console prompt to type
into. On launch:

1. A **spotlight box** pops up automatically asking what you're trying to
   do. Type your goal (e.g. "turn on dark mode in Settings"), hit Enter —
   it starts reasoning about the first step immediately, no extra keypress.
   Or skip typing entirely: **triple-click anywhere** and just say it — see
   "Voice input" below.
2. A small **HUD dock** appears in the top-right corner: an animated status
   dot (Watching / Listening / Thinking / Guiding / Done), your goal, a
   Guide/Autonomous mode link, and a Stop button. Drag it anywhere; click
   the goal text any time to reopen the spotlight and change it — that also
   re-triggers reasoning right away.
3. Switch to the app you're actually working in. **You don't need to press
   anything** — a background loop watches for the screen to change
   meaningfully (and settle, so it doesn't fire mid-scroll), then
   automatically asks Gemini for the next step and draws an animated
   **ghost cursor**: a small flat-design pointer (Figma/Google Docs-style
   collaborator-cursor look, not an OS cursor mimic), it starts at your
   actual cursor position and glides there in a direct line, then sits still —
   plus an instruction card. It never clicks anything on its own here.
   If Gemini can't actually
   locate the target in the current screenshot, it shows the instruction as
   a plain banner instead of guessing a fake location — see "Coordinate
   accuracy" below.
4. If Gemini judges a step is worth doing *for* you, an animated prompt
   pops up asking **Just once / This session / No**.
5. Keep working — each screen change triggers the next step automatically.
   When Gemini decides the goal is achieved, the HUD status turns "Done"
   and the loop stops re-triggering until you set a new/edited goal.
6. **Stop actually pauses it** — hitting Stop (hotkey or HUD button) halts
   the continuous loop, not just the current step; it stays paused until
   you set/edit the goal again or force a check.

### Hotkeys (all optional shortcuts to the same overlay actions)

| Hotkey | Does |
|---|---|
| `Ctrl+Alt+Space` | Open the spotlight to set/edit the goal (same as clicking the HUD goal text) |
| `Ctrl+Alt+G` | Force a check right now, bypassing the change-detection wait |
| `Ctrl+Alt+Q` | **Stop** — instantly clears the overlay and cancels anything in flight |
| `Ctrl+Alt+A` | Toggle Guide ↔ Autonomous mode (same as clicking the HUD mode button) |

You can also always yank the mouse into a screen corner to abort a
pyautogui action mid-flight (pyautogui's built-in failsafe), independent of
the Stop hotkey.

### Voice input

**Triple-click anywhere on screen** (not double — see below) to start
listening; the HUD status turns "Listening" (purple). Just say your goal
("turn on dark mode in Settings") and stop talking — recording ends itself
on silence, no second click needed. It's transcribed and set as the goal
immediately, same as typing it into the spotlight.

Transcription runs through a dedicated free speech-to-text engine
(`SpeechRecognition`'s default Google Web Speech recognizer), not Gemini —
voice capture spends no Gemini tokens/quota. It needs internet access (that
recognizer is a cloud call) and a working microphone; if `pyautogui`-style
input access is locked down or no mic is available, it fails with a message
in the console rather than hanging.

**Why triple-click, not double:** double-clicks happen constantly in normal
computer use (opening files, selecting words) and would misfire voice
capture over and over. Three rapid clicks are rare enough to be a safe,
deliberate gesture. This is a global mouse hook (the `mouse` package) —
it fires anywhere on screen regardless of which window has focus, for as
long as clicker is running. Tune `CLICK_TRIGGER_COUNT` /
`CLICK_TRIGGER_WINDOW_MS` in [clicker/config.py](clicker/config.py) if you
want it more/less sensitive (e.g. back to double-click, accepting the
accidental-trigger risk).

### API quota — read this before judging "laggy"

Default model is `gemini-3.5-flash-lite` (see `GEMINI_MODEL` in
[clicker/config.py](clicker/config.py)). Free-tier daily quotas are
account/model-specific and not published as a fixed number — check yours at
[aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit).
`gemini-3.6-flash` was tried first and hit a **20 requests/day** free-tier
cap almost immediately — unusable for continuous mode. If steps stop
appearing and the HUD sits on "Thinking" or silently reverts to "Watching,"
check the console for a `429 RESOURCE_EXHAUSTED` before assuming something's
broken. Real continuous operation is only reliable with billing enabled on
the Google AI Studio / Cloud project behind your key.

### Sensitive-app guard

While a password manager or a handful of financial-app window titles are
focused (see `SENSITIVE_APP_PATTERNS` in [clicker/config.py](clicker/config.py)),
clicker forces guide-only behavior — it will never offer to act there, even
in Autonomous mode or with a standing session grant. This is a heuristic
based on the window title, not a real security boundary — treat it as a
safety bias, not a guarantee, and extend the pattern list for anything else
you consider sensitive.

## What's here

| File | Role |
|---|---|
| [clicker/see.py](clicker/see.py) | screenshot the primary screen via `mss` |
| [clicker/changeloop.py](clicker/changeloop.py) | background loop: diffs consecutive screenshots, triggers a cycle once the screen changes and settles |
| [clicker/winctx.py](clicker/winctx.py) | foreground window title (context for Gemini + the sensitive-app guard) |
| [clicker/understand.py](clicker/understand.py) | Gemini call, JSON contract (now includes `goal_complete`) |
| [clicker/overlay.py](clicker/overlay.py) | all UI: HUD dock, spotlight goal entry, ghost-cursor guide marker, animated permission prompt |
| [clicker/voice.py](clicker/voice.py) | listen-until-silence + free STT transcription (no Gemini tokens spent) |
| [clicker/clicktrigger.py](clicker/clicktrigger.py) | global triple-click detector that summons voice input |
| [clicker/act.py](clicker/act.py) | `pyautogui` click/type, only after a grant |
| [clicker/main.py](clicker/main.py) | wiring: hotkeys, HUD callbacks, the cycle itself |

## Known limitations

- Single (primary) monitor only — still out of scope per the brief.
- No system tray icon; mode toggle lives on the HUD + a hotkey instead.
- No persistence across runs (session permission grant, and the goal, reset
  each launch).
- The sensitive-app guard matches on window title text only — trivially
  spoofable, not a hard security boundary.
- Coordinate accuracy is still the thing to validate most carefully across
  different apps before leaning on `act`. Two real bugs already found and
  fixed here, both worth re-validating on your own apps rather than trusting
  blind:
  - Asking Gemini for raw pixel `x`/`y` on the screenshot (as BRIEF.md
    originally specified) was unreliable — it sometimes answered as if the
    image were a different resolution than what was actually sent, and a
    custom `{"x":..,"y":..}` shape occasionally came back with the axes
    swapped depending on image size. Switched to Gemini's own trained
    convention — `"point": [y, x]` normalized to 0-1000 — which was accurate
    across every size tested.
  - Even with grounded coordinates, the model will confidently answer from
    generic "where things usually are" knowledge instead of the actual
    pixels — verified directly: asked to point at the Windows Start button
    in a screenshot where no taskbar was even visible, it invented a
    plausible bottom-left coordinate anyway, and separately assumed the old
    bottom-left Start button convention when Windows 11's default taskbar is
    center-aligned. `target.visible` plus an explicit "don't guess from
    convention" instruction fixed both in testing — see the docstring in
    [clicker/understand.py](clicker/understand.py). When `visible` comes
    back false, the overlay shows a plain instruction banner instead of a
    pointer, rather than drawing an arrow at a fabricated location.
- Free-tier API quota makes real continuous operation impractical without
  enabling billing — see above.
- The guide overlay is click-through for *empty* space (Tk's
  `-transparentcolor`), but clicks landing exactly on something it actually
  draws (the ghost cursor, the connector line, the instruction card)
  still get swallowed instead of reaching the app underneath. A proper
  Win32 fix (`WS_EX_TRANSPARENT`) was investigated and looked correct at
  the OS hit-testing level in isolation, but every real click-delivery test
  came back worse, and the test harness itself turned out to be unreliable
  in this environment (Windows' foreground-lock behavior meant a
  background-spawned test window couldn't reliably win real focus/z-order
  priority). Reverted rather than ship something unverified — this is worth
  someone re-testing interactively (not via an automated harness) before
  trusting it. In practice the risk is narrow: the card sits offset away
  from the target by design, so it's mainly the pin/line footprint right at
  the target that could occasionally overlap a very small real button.
