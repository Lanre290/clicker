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
   do. Type your goal (e.g. "turn on dark mode in Settings"), hit Enter.
2. A small **HUD dock** appears in the top-right corner showing status
   (Watching / Thinking / Guiding / Done), your goal, a Guide/Autonomous
   mode switch, and a Stop button. Drag it anywhere; click the goal text any
   time to reopen the spotlight and change it.
3. Switch to the app you're actually working in. **You don't need to press
   anything** — a background loop watches for the screen to change
   meaningfully (and settle, so it doesn't fire mid-scroll), then
   automatically asks Gemini for the next step and draws an animated
   arrow + ring pointing at it, with a one-line instruction. It never
   clicks anything on its own here.
4. If Gemini judges a step is worth doing *for* you, an animated prompt
   pops up asking **Just once / This session / No**.
5. Keep working — each screen change triggers the next step automatically.
   When Gemini decides the goal is achieved, the HUD status turns "Done"
   and the loop stops re-triggering until you set a new/edited goal.

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

### API quota — read this before judging "laggy"

The Gemini free tier caps `gemini-3.6-flash` at **20 requests per day**. In
continuous mode, that's roughly 20 guide steps *total*, not per session —
it'll exhaust in well under a minute of active guiding and then every cycle
fails with a `429 RESOURCE_EXHAUSTED` (visible in the console, since that's
still where errors get logged). If steps stop appearing and the HUD sits on
"Thinking" or silently reverts to "Watching," check the console for that
error before assuming something's broken. Continuous mode is only really
usable with billing enabled on the Google AI Studio / Cloud project behind
your key — the free tier was designed for occasional testing, not a
screen-watching loop.

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
| [clicker/overlay.py](clicker/overlay.py) | all UI: HUD dock, spotlight goal entry, animated guide marker, animated permission prompt |
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
  different apps before leaning on `act`. One real bug already found and
  fixed here: asking Gemini for raw pixel `x`/`y` on the screenshot (as
  BRIEF.md originally specified) was unreliable — it sometimes answered as
  if the image were a different resolution than what was actually sent, and
  a custom `{"x":..,"y":..}` shape occasionally came back with the axes
  swapped depending on image size. Switched to Gemini's own trained
  convention — `"point": [y, x]` normalized to 0-1000 — which was accurate
  across every size tested; see the docstring in
  [clicker/understand.py](clicker/understand.py) for the full story. Worth
  re-validating this on your own apps rather than trusting it blind.
- Free-tier API quota (20 requests/day for `gemini-3.6-flash`) makes real
  continuous operation impractical without enabling billing — see above.
