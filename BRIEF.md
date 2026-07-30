# Build brief — screen guide with permission-to-act

## What we're building
A Windows desktop app that watches the user's screen and **guides** them through whatever they're doing — points at the right button, explains the next step. It never touches the mouse or keyboard by default.

When the app judges that *doing* a step itself would help more than pointing at it, it **asks permission first**. The user chooses the scope: do it once, act freely for the rest of the session, or keep guiding only. There's also a tray toggle to switch between Guide mode and Autonomous mode manually.

Think of it as a screen-aware guide (like a teacher pointing at your screen) that can earn the right to take the wheel — but only when the user hands it over.

## Core principles
- **Guide is the default.** Watch, point, explain. Do not touch anything.
- **Acting is opt-in.** Only act after the user grants permission, or after they've flipped on Autonomous mode.
- **The user holds the leash.** Every permission ask offers: once / this session / no.
- **Say why.** When it offers to act, it gives a short, concrete reason.
- **A stop key always works.** One hotkey halts everything instantly.

## The core loop
1. **See** — capture a screenshot of the active screen (on hotkey, not continuously).
2. **Understand** — send the screenshot + the user's goal to Gemini. Get back a structured response.
3. **Guide** — draw an arrow/box on screen pointing at the right element, with a one-line instruction. No touching.
4. **Judge** — Gemini also returns whether this is a moment to offer to act, plus a reason.
5. **Offer + scope** — if flagged (and not already in Autonomous mode), show a small prompt: once / this session / keep guiding.
6. **Act** — only if granted: move the mouse and click/type the target.

## Gemini response contract (JSON)
Gemini must reply in this exact shape, nothing else:
```json
{
  "instruction": "short text telling the user what to do next",
  "target": { "x": 0, "y": 0, "label": "what the arrow points at" },
  "offer_to_act": false,
  "reason": "why acting would help (only when offer_to_act is true)",
  "action": { "type": "click | type", "value": "text to type if type" }
}
```
Prompt Gemini to return coordinates as pixel positions on the screenshot it was given.

## Tech stack (keep it simple)
- **Python** — everything.
- **Gemini API** (`google-generativeai`) — the eyes + brain (vision + judgment).
- **mss** — screenshots.
- **pyautogui** — moves mouse / clicks / types (only when permitted).
- **Tkinter** — the overlay for now: transparent, always-on-top, draws the arrow/box + the permission prompt. (We'll rebuild the overlay in PyWebView with HTML/CSS later for polish — not now.)
- **keyboard** — global hotkey to summon it + a stop key.

Not needed yet (later only): pywinauto, SQLite, system tray, PyInstaller packaging.

## The hard part — attack this first
Getting Gemini's coordinates accurate enough to point at (and later click) the *right* pixel. This is make-or-break. Before building any UI, prove that: screenshot → Gemini → a box drawn on screen lands on the correct element, reliably, across a few different apps (Settings, a browser, a creative tool).

## Week-1 goal (the spike)
One honest end-to-end run:
- User states a goal and picks a real app.
- App guides them through 3–4 steps by pointing (Guide mode, no touching).
- At one step, it offers to act with a reason.
- User grants "just once."
- App performs that one click/type correctly.

Ugly is fine. Console + a rough overlay is fine. The only thing that matters is that pointing is accurate and the guide → offer → act flow works.

## What to skip in week 1
Pretty UI, animations, PyWebView, tray icon, settings screen, accounts, installer, multi-monitor edge cases, voice. None of these are the risk.

## Build order
1. `see` — screenshot the active screen.
2. `understand` — Gemini call returning the JSON contract above.
3. `guide` — Tkinter overlay draws the arrow/box + instruction at the returned coordinates. **Validate accuracy here before moving on.**
4. `offer` — permission prompt with once / session / no.
5. `act` — pyautogui performs the granted action.
6. `stop` — global hotkey that halts everything.
