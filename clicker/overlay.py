"""All on-screen UI: the persistent HUD dock (status/goal/mode/stop), the
spotlight goal-entry box, the animated guide marker (adaptive-placement card
+ ghost-cursor pin), and the animated permission prompt (once / this session
/ no).

Tk must live on the main thread. `run_cycle` / `watch_loop` (see main.py)
execute on background threads, so all UI mutation is marshalled onto the Tk
thread via a thread-safe queue that `_poll()` drains every 50ms.
`ask_permission_sync` blocks the calling (background) thread on a result
queue until a button is clicked, which keeps main.py's cycle logic linear.
"""

import math
import queue
import time
import tkinter as tk
from pathlib import Path

import pyautogui
from PIL import Image, ImageTk

from . import config

_TRANSPARENT_KEY = "magenta"

_ASSETS_DIR = Path(__file__).parent / "assets"
_CURSOR_ASSET_PATH = _ASSETS_DIR / "cursor.png"


def _detect_cursor_hotspot(image):
    """Derives the cursor's hotspot (its pointing tip) directly from whatever
    image is actually in cursor.png — no hardcoded constant tied to one
    specific asset, so dropping in a different image (any size, any padding)
    still positions correctly. The top-left corner of the tight bounding box
    around the image's opaque pixels — correct as long as the asset's tip
    points up and to the left, which is the natural convention for this kind
    of pointer glyph (and matches how gen_cursor_asset.py builds its own).
    """
    hotspot = (0, 0)
    if "A" in image.mode:
        # Threshold before finding the bbox: anti-aliased edges leave a rim
        # of near-zero-but-nonzero alpha that getbbox() alone would count,
        # placing the hotspot a few pixels outside where the tip visually
        # looks solid.
        mask = image.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
        bbox = mask.getbbox()
        if bbox is not None:
            hotspot = (bbox[0], bbox[1])
    return hotspot

_BG = "#ffffff"
_BG_SUBTLE = "#f5f5f7"
_BORDER = "#e5e5ea"
_ACCENT = "#0a84ff"
_TEXT = "#1c1c1e"
_TEXT_DIM = "#6e6e73"
_TEXT_FAINT = "#9a9a9e"

STATUS_COLORS = {
    "watching": _ACCENT,
    "thinking": "#ff9f0a",
    "guiding": "#5e5ce6",
    "listening": "#bf5af2",
    "paused": _TEXT_FAINT,
    "done": "#30d158",
}

# Stacked offset rounded rects behind a card, light-to-dark from the card
# outward. Overlay windows are chroma-keyed transparent (see
# _TRANSPARENT_KEY below), which only supports a binary transparent/opaque
# pixel -- there's no real per-pixel alpha to blend a soft blurred shadow
# against whatever is on the desktop underneath, so this bands its way to
# an approximation instead.
_SHADOW_BANDS = [
    (2, 3, "#d6d6da"),
    (4, 6, "#e2e2e6"),
    (6, 9, "#ececef"),
    (8, 12, "#f5f5f6"),
]

# Icon files clicker looks for in clicker/assets/. Each is expected to be a
# transparent-background PNG containing a single glyph in any color -- only
# its alpha channel is used (see Overlay._icon), so the source color doesn't
# matter. Recolored on load to match the UI. If a file is missing, callers
# fall back to a small hand-drawn placeholder so the app still runs before
# real artwork is dropped in.
ICON_MIC = "icon_mic.png"
ICON_STOP = "icon_stop.png"
ICON_MODE_GUIDE = "icon_mode_guide.png"
ICON_MODE_AUTONOMOUS = "icon_mode_autonomous.png"
ICON_GUIDE_TARGET = "icon_guide_target.png"

HUD_W, HUD_H = 300, 84
HUD_PAD = 16  # left/right inner margin shared by the goal text and control row

# Control row: mode toggle (left, sized for its longest label so "Autonomous"
# never clips), mic (center), Stop (right, sized for its fixed label). Widths
# aren't equal, so the mic is centered in the *gap* between the two buttons
# rather than at the card's literal midpoint -- that keeps the whitespace on
# either side of it even, which reads as tidier than forcing the mic to the
# card's geometric center and leaving one side crowded.
MODE_BTN_W = 112
STOP_BTN_W = 64
MIC_D = 28


def _ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _ease_in_cubic(t):
    t = max(0.0, min(1.0, t))
    return t ** 3


def _round_rect(canvas, x1, y1, x2, y2, radius=16, **kwargs):
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _draw_shadow(canvas, x1, y1, x2, y2, radius=16):
    """Fakes a soft drop shadow behind a card with stacked, offset, lighter-
    going-outward rounded rects (see _SHADOW_BANDS). Draw this immediately
    before the card itself so the card lands on top in z-order without
    needing an explicit raise.
    """
    for dx, dy, color in reversed(_SHADOW_BANDS):
        _round_rect(canvas, x1 + dx, y1 + dy, x2 + dx, y2 + dy, radius=radius, fill=color, outline="")


def _draw_close_button(canvas, bx2, by1, on_click):
    """A small 'x' inset from a card's top-right corner, so the user can
    dismiss a guide before it times out on its own instead of it lingering
    in their way. Two crossed lines rather than an icon asset — a glyph
    this simple draws crisply at any size without needing real artwork.
    Draw this last (after the card) so it lands on top in z-order.
    """
    r = 9
    cx, cy = bx2 - r - 8, by1 + r + 8
    d = 4
    hit = canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill="", outline="")
    l1 = canvas.create_line(cx - d, cy - d, cx + d, cy + d, fill=_TEXT_FAINT, width=2, capstyle=tk.ROUND)
    l2 = canvas.create_line(cx - d, cy + d, cx + d, cy - d, fill=_TEXT_FAINT, width=2, capstyle=tk.ROUND)
    ids = [hit, l1, l2]

    def enter(_e):
        canvas.itemconfig(hit, fill=_BG_SUBTLE)
        canvas.itemconfig(l1, fill=_TEXT)
        canvas.itemconfig(l2, fill=_TEXT)
        canvas.config(cursor="hand2")

    def leave(_e):
        canvas.itemconfig(hit, fill="")
        canvas.itemconfig(l1, fill=_TEXT_FAINT)
        canvas.itemconfig(l2, fill=_TEXT_FAINT)
        canvas.config(cursor="")

    for iid in ids:
        canvas.tag_bind(iid, "<Button-1>", lambda e: on_click())
        canvas.tag_bind(iid, "<Enter>", enter)
        canvas.tag_bind(iid, "<Leave>", leave)
    return ids


class Overlay:
    def __init__(self):
        self._root = tk.Tk()
        self._root.withdraw()  # no visible root window, just the event loop
        self._queue = queue.Queue()
        self._guide_windows = []
        self._prompt_windows = []
        self._hud = None
        self._hud_canvas = None
        self._hud_status_text = None
        self._hud_goal_text = None
        self._hud_mode_btn = None
        self._spotlight = None
        # Keep a strong reference — Tkinter doesn't, and the PhotoImage gets
        # silently garbage-collected (and vanishes from screen) without one.
        cursor_pil_image = Image.open(_CURSOR_ASSET_PATH)
        self._cursor_image = ImageTk.PhotoImage(cursor_pil_image)
        self._cursor_hotspot = _detect_cursor_hotspot(cursor_pil_image)
        self._icon_cache = {}  # (filename, size, color) -> PhotoImage; also keeps them alive
        self._current_guide_dismiss = None  # zero-arg callable: instantly dismiss the on-screen guide, if any
        self._poll()

    # -- plumbing ----------------------------------------------------------

    def _icon(self, filename, size, color):
        """Loads clicker/assets/{filename}, recolored to `color` (hex string)
        using the source image's alpha channel as a mask -- so any icon
        works regardless of its own fill color, as long as its background is
        transparent. Returns None if the file hasn't been dropped in yet, so
        callers can fall back to a hand-drawn placeholder.
        """
        key = (filename, size, color)
        cached = self._icon_cache.get(key)
        if cached is not None:
            return cached
        path = _ASSETS_DIR / filename
        if not path.exists():
            return None
        try:
            src = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception:
            return None
        solid = Image.new("RGBA", src.size, _hex_to_rgb(color) + (255,))
        solid.putalpha(src.getchannel("A"))
        photo = ImageTk.PhotoImage(solid)
        self._icon_cache[key] = photo
        return photo

    def _mode_icon(self, mode):
        filename = ICON_MODE_GUIDE if mode == "Guide" else ICON_MODE_AUTONOMOUS
        return self._icon(filename, 14, _ACCENT)

    def _poll(self):
        try:
            while True:
                fn = self._queue.get_nowait()
                fn()
        except queue.Empty:
            pass
        self._root.after(50, self._poll)

    def call(self, fn):
        """Schedule fn to run on the Tk thread. Safe to call from any thread."""
        self._queue.put(fn)

    def run(self):
        """Blocks. Call once, from the main thread, after wiring everything up."""
        self._root.mainloop()

    # -- animation helpers ---------------------------------------------------

    def _fade_in(self, win, target=0.97, ms=None, on_done=None):
        ms = ms or config.FADE_MS
        steps = max(1, ms // 15)

        def step(i=0):
            if not win.winfo_exists():
                return
            try:
                win.attributes("-alpha", target * _ease_out_cubic(i / steps))
            except tk.TclError:
                return
            if i < steps:
                win.after(15, lambda: step(i + 1))
            elif on_done:
                on_done()

        step()

    def _fade_out(self, win, ms=None, on_done=None):
        ms = ms or config.FADE_MS
        steps = max(1, ms // 15)
        try:
            start_alpha = float(win.attributes("-alpha"))
        except tk.TclError:
            start_alpha = 1.0

        def step(i=0):
            if not win.winfo_exists():
                return
            try:
                win.attributes("-alpha", max(0.0, start_alpha * (1 - _ease_in_cubic(i / steps))))
            except tk.TclError:
                pass
            if i < steps:
                win.after(15, lambda: step(i + 1))
            elif on_done:
                on_done()

        step()

    @staticmethod
    def _make_drag_handlers(win):
        def start(event):
            win._drag_x, win._drag_y = event.x, event.y

        def move(event):
            x = win.winfo_x() + (event.x - win._drag_x)
            y = win.winfo_y() + (event.y - win._drag_y)
            win.geometry(f"+{x}+{y}")

        return start, move

    @staticmethod
    def _add_hover(widget, base_bg, hover_bg):
        widget.bind("<Enter>", lambda e: widget.config(bg=hover_bg))
        widget.bind("<Leave>", lambda e: widget.config(bg=base_bg))

    # -- HUD (replaces the console entirely) ---------------------------------

    def ensure_hud(self, on_toggle_mode, on_stop, on_edit_goal, on_voice_trigger):
        def _do():
            if self._hud is not None:
                return
            win = tk.Toplevel(self._root)
            self._hud = win
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            sw = win.winfo_screenwidth()
            margin = 20
            win.geometry(f"{HUD_W}x{HUD_H}+{sw - HUD_W - margin}+{margin}")
            win.config(bg=_TRANSPARENT_KEY)
            try:
                win.attributes("-transparentcolor", _TRANSPARENT_KEY)
            except tk.TclError:
                pass
            win.attributes("-alpha", 0.0)
            win._status_shape_ids = []
            win._status_job = None

            canvas = tk.Canvas(win, bg=_TRANSPARENT_KEY, highlightthickness=0, width=HUD_W, height=HUD_H)
            canvas.pack(fill="both", expand=True)
            _draw_shadow(canvas, 1, 1, HUD_W - 1, HUD_H - 1, radius=18)
            _round_rect(canvas, 1, 1, HUD_W - 1, HUD_H - 1, radius=18, fill=_BG, outline=_BORDER)
            self._hud_canvas = canvas

            # No separate accent strip: the status dot is already the color
            # signal, a strip repeating the same color on top was clutter.
            self._hud_status_text = canvas.create_text(
                36, 20, text="Watching", fill=_TEXT_DIM, font=("Segoe UI", 9), anchor="w",
            )
            self._hud_goal_text = canvas.create_text(
                HUD_PAD, 44, text="No goal set — click to add one", fill=_TEXT,
                font=("Segoe UI", 10, "bold"), anchor="w", width=HUD_W - 2 * HUD_PAD,
            )

            # Mode toggle reads as a plain link (blends into the card, only
            # a hover fill gives it away) so Stop stays the one real button.
            mode_icon = self._mode_icon("Guide")
            mode_btn = tk.Button(
                win, text=" Guide", image=mode_icon, compound="left",
                font=("Segoe UI", 9, "bold"), bg=_BG, fg=_ACCENT,
                activebackground=_BG_SUBTLE, activeforeground=_ACCENT, relief="flat", bd=0,
                cursor="hand2", command=on_toggle_mode,
            )
            self._add_hover(mode_btn, _BG, _BG_SUBTLE)
            row_y = 64
            mode_x1 = HUD_PAD
            mode_x2 = mode_x1 + MODE_BTN_W
            canvas.create_window(mode_x1, row_y, window=mode_btn, anchor="w", width=MODE_BTN_W, height=26)
            self._hud_mode_btn = mode_btn

            stop_icon = self._icon(ICON_STOP, 13, _TEXT_DIM)
            stop_btn = tk.Button(
                win, text=" Stop", image=stop_icon, compound="left",
                font=("Segoe UI", 9, "bold"), bg=_BG_SUBTLE, fg=_TEXT_DIM,
                activebackground=_BORDER, activeforeground=_TEXT, relief="flat", bd=0,
                cursor="hand2", command=on_stop,
            )
            self._add_hover(stop_btn, _BG_SUBTLE, _BORDER)
            stop_x2 = HUD_W - HUD_PAD
            stop_x1 = stop_x2 - STOP_BTN_W
            canvas.create_window(stop_x2, row_y, window=stop_btn, anchor="e", width=STOP_BTN_W, height=26)

            # Mic button: round hit-area drawn on canvas (not a tk.Button)
            # so it can actually be circular, matching the flat-design
            # language elsewhere. Same trigger as the triple-click gesture.
            gap = (stop_x1 - mode_x2 - MIC_D) / 2
            mic_cx, mic_cy, mic_r = mode_x2 + gap + MIC_D / 2, row_y, MIC_D / 2
            mic_accent = STATUS_COLORS["listening"]
            mic_bg = canvas.create_oval(
                mic_cx - mic_r, mic_cy - mic_r, mic_cx + mic_r, mic_cy + mic_r,
                fill=_BG_SUBTLE, outline="",
            )
            mic_icon = self._icon(ICON_MIC, 18, mic_accent)
            if mic_icon is not None:
                mic_glyph_id = canvas.create_image(mic_cx, mic_cy, image=mic_icon)
                mic_ids = [mic_bg, mic_glyph_id]
            else:
                mic_body = canvas.create_oval(
                    mic_cx - 5, mic_cy - 9, mic_cx + 5, mic_cy + 2, fill=mic_accent, outline="",
                )
                mic_stand = canvas.create_line(mic_cx, mic_cy + 2, mic_cx, mic_cy + 8, fill=mic_accent, width=2)
                mic_base = canvas.create_line(mic_cx - 5, mic_cy + 8, mic_cx + 5, mic_cy + 8, fill=mic_accent, width=2)
                mic_ids = [mic_bg, mic_body, mic_stand, mic_base]

            def mic_enter(_e):
                canvas.itemconfig(mic_bg, fill=_BORDER)
                canvas.config(cursor="hand2")

            def mic_leave(_e):
                canvas.itemconfig(mic_bg, fill=_BG_SUBTLE)
                canvas.config(cursor="")

            for iid in mic_ids:
                canvas.tag_bind(iid, "<Button-1>", lambda e: on_voice_trigger())
                canvas.tag_bind(iid, "<Enter>", mic_enter)
                canvas.tag_bind(iid, "<Leave>", mic_leave)

            canvas.tag_bind(self._hud_goal_text, "<Button-1>", lambda e: on_edit_goal())
            canvas.tag_bind(self._hud_status_text, "<Button-1>", lambda e: on_edit_goal())
            for tag in (self._hud_goal_text, self._hud_status_text):
                canvas.tag_bind(tag, "<Enter>", lambda e: canvas.config(cursor="hand2"))
                canvas.tag_bind(tag, "<Leave>", lambda e: canvas.config(cursor=""))

            drag_start, drag_move = self._make_drag_handlers(win)
            canvas.bind("<ButtonPress-1>", drag_start, add="+")
            canvas.bind("<B1-Motion>", drag_move, add="+")

            self._animate_hud_status("watching")
            self._fade_in(win)

        self.call(_do)

    def _animate_hud_status(self, status):
        win, c = self._hud, self._hud_canvas
        if win is None or not win.winfo_exists():
            return
        if win._status_job is not None:
            try:
                win.after_cancel(win._status_job)
            except tk.TclError:
                pass
            win._status_job = None
        for iid in win._status_shape_ids:
            c.delete(iid)
        win._status_shape_ids = []

        color = STATUS_COLORS.get(status, _TEXT_FAINT)
        cx, cy = 22, 20

        if status == "thinking":
            arc = c.create_arc(cx - 9, cy - 9, cx + 9, cy + 9, start=0, extent=110,
                                style="arc", outline=color, width=3)
            win._status_shape_ids = [arc]
            t0 = time.time()

            def spin():
                if not win.winfo_exists():
                    return
                phase = ((time.time() - t0) * 1000 % config.SPIN_PERIOD_MS) / config.SPIN_PERIOD_MS
                c.itemconfig(arc, start=360 * phase)
                win._status_job = win.after(30, spin)

            spin()
        elif status == "done":
            l1 = c.create_line(cx - 7, cy, cx - 2, cy + 5, fill=color, width=3, capstyle=tk.ROUND)
            l2 = c.create_line(cx - 2, cy + 5, cx + 8, cy - 7, fill=color, width=3, capstyle=tk.ROUND)
            win._status_shape_ids = [l1, l2]
        elif status == "paused":
            dot = c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=color, outline="")
            win._status_shape_ids = [dot]
        else:  # watching / guiding: a breathing dot, faster + brighter while guiding
            dot = c.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill=color, outline="")
            win._status_shape_ids = [dot]
            t0 = time.time()
            period = config.PULSE_PERIOD_MS * (0.55 if status == "guiding" else 1.0)

            def breathe():
                if not win.winfo_exists():
                    return
                phase = ((time.time() - t0) * 1000 % period) / period
                r = 5 + 2.2 * math.sin(phase * 2 * math.pi)
                c.coords(dot, cx - r, cy - r, cx + r, cy + r)
                win._status_job = win.after(40, breathe)

            breathe()

    def update_hud(self, status=None, goal=None, mode=None):
        def _do():
            if self._hud is None or not self._hud.winfo_exists():
                return
            c = self._hud_canvas
            if status is not None:
                self._animate_hud_status(status)
                c.itemconfig(self._hud_status_text, text=status.capitalize())
            if goal is not None:
                text = goal if goal else "No goal set — click to add one"
                if len(text) > 40:
                    text = text[:39] + "…"
                c.itemconfig(self._hud_goal_text, text=text)
            if mode is not None:
                icon = self._mode_icon(mode)
                self._hud_mode_btn.config(text=f" {mode}", image=icon, compound="left")

        self.call(_do)

    # -- spotlight (goal entry, replaces console input) -----------------------

    def show_spotlight(self, current_goal, on_submit):
        def _do():
            if self._spotlight is not None:
                return
            win = tk.Toplevel(self._root)
            self._spotlight = win
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            w, h = 540, 76
            sw = win.winfo_screenwidth()
            win.geometry(f"{w}x{h}+{(sw - w) // 2}+120")
            win.config(bg=_TRANSPARENT_KEY)
            try:
                win.attributes("-transparentcolor", _TRANSPARENT_KEY)
            except tk.TclError:
                pass
            win.attributes("-alpha", 0.0)

            canvas = tk.Canvas(win, bg=_TRANSPARENT_KEY, highlightthickness=0, width=w, height=h)
            canvas.pack(fill="both", expand=True)
            _draw_shadow(canvas, 1, 1, w - 1, h - 1, radius=18)
            _round_rect(canvas, 1, 1, w - 1, h - 1, radius=18, fill=_BG, outline=_BORDER)

            var = tk.StringVar(value=current_goal or "")
            entry = tk.Entry(
                win, textvariable=var, font=("Segoe UI", 13), bg=_BG, fg=_TEXT,
                insertbackground=_TEXT, relief="flat", justify="left",
            )
            canvas.create_window(20, h // 2, window=entry, anchor="w", width=w - 150)
            canvas.create_text(w - 20, h // 2, text="Enter ↵  ·  Esc cancel", fill=_TEXT_FAINT,
                                font=("Segoe UI", 9), anchor="e")

            def submit(event=None):
                goal = var.get().strip()
                self._close_spotlight()
                if goal:
                    on_submit(goal)

            def cancel(event=None):
                self._close_spotlight()

            entry.bind("<Return>", submit)
            entry.bind("<Escape>", cancel)
            win.protocol("WM_DELETE_WINDOW", cancel)

            def focus_entry():
                entry.focus_force()
                entry.icursor(tk.END)

            self._fade_in(win, on_done=focus_entry)

        self.call(_do)

    def _close_spotlight(self):
        win = self._spotlight
        if win is None:
            return
        self._spotlight = None
        self._fade_out(win, on_done=lambda: self._safe_destroy(win))

    # -- guide (point + instruct, no touching) --------------------------------
    #
    # Design: a ghost-cursor pin travels from the user's real mouse position
    # to the target, then bobs gently to hold attention. A card with the
    # instruction is placed on whichever side of the target has the most
    # screen room, then clamped fully on-screen if the estimate was off. A
    # dashed connector line is drawn from the pin to the *closest point*
    # on the card's actual final bounding box, so it always looks correct
    # regardless of where the card ended up.

    def show_guide(self, instruction, x, y, label, seconds=None, visible=True, on_dismiss=None):
        """visible=False means Gemini couldn't actually locate `label` on
        screen (see understand.py's target.visible) — x/y are meaningless in
        that case, so this renders a plain centered instruction banner
        instead of a pointer, rather than drawing an arrow at a fabricated
        location.

        on_dismiss: called if the card is closed early — via its own 'x' or
        via dismiss_current_guide() (see there: fired the instant a real
        click/Enter/Tab is detected elsewhere) — but not on a normal
        timeout, so the caller can react to "the user's already moved on"
        as distinct from "its time was up".
        """
        seconds = seconds or config.OVERLAY_DISPLAY_SECONDS

        def _do():
            self._clear_guide_windows()

            win = tk.Toplevel(self._root)
            self._guide_windows.append(win)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            win.geometry(f"{sw}x{sh}+0+0")
            win.config(bg=_TRANSPARENT_KEY)
            try:
                win.attributes("-transparentcolor", _TRANSPARENT_KEY)
            except tk.TclError:
                pass
            win.attributes("-alpha", 0.0)

            canvas = tk.Canvas(win, bg=_TRANSPARENT_KEY, highlightthickness=0, width=sw, height=sh)
            canvas.pack(fill="both", expand=True)

            expire_job = None

            def close(dismissed):
                nonlocal expire_job
                if expire_job is not None:
                    try:
                        win.after_cancel(expire_job)
                    except tk.TclError:
                        pass
                    expire_job = None
                if dismissed:
                    # No fade here — "dismissed" means the user's already
                    # moved on (clicked the 'x', or clicked/typed elsewhere
                    # entirely), so it should just be gone, not linger
                    # through a couple hundred ms of fade-out.
                    self._destroy_window(win, self._guide_windows)
                elif win.winfo_exists():
                    self._fade_out(win, on_done=lambda: self._destroy_window(win, self._guide_windows))
                if dismissed and on_dismiss is not None:
                    on_dismiss()

            on_close = lambda: close(dismissed=True)
            self._current_guide_dismiss = on_close

            if visible:
                start_travel = self._render_pointing_guide(canvas, win, sw, sh, instruction, x, y, label, on_close)
            else:
                self._render_bannerless_guide(canvas, win, sw, instruction, on_close)
                start_travel = None

            expire_job = win.after(int(seconds * 1000), lambda: close(dismissed=False))
            # Fade in first (card + cursor appear, cursor sitting at the
            # user's real position), *then* glide the cursor to the target —
            # sequenced, not concurrent, so the glide is actually visible
            # instead of finishing invisibly while alpha is still ramping up.
            self._fade_in(win, on_done=start_travel)

        self.call(_do)

    def dismiss_current_guide(self):
        """Instantly clears whatever guide card is on screen right now, if
        any — same effect as the user clicking the card's own 'x'. Called
        the moment a real click/Enter/Tab is detected anywhere (see
        clicktrigger.register_action_check's on_immediate), so a now-stale
        instruction doesn't linger on screen while the next screenshot is
        captured, and no-ops harmlessly if there's nothing showing.
        """
        def _do():
            if self._current_guide_dismiss is not None:
                self._current_guide_dismiss()
        self.call(_do)

    def _render_pointing_guide(self, canvas, win, sw, sh, instruction, x, y, label, on_close):
        # Ghost cursor: a small flat-design pointer image (see
        # gen_cursor_asset.py), starts at the user's actual current cursor
        # position — where their eye already is — and glides there in a
        # direct, straight-line motion. No idle motion once it lands; a
        # real cursor doesn't bob or pulse when it's sitting still.
        try:
            start_x, start_y = pyautogui.position()
        except Exception:
            start_x, start_y = x, y

        # Hotspot is derived from whatever image is actually loaded (see
        # _detect_cursor_hotspot) — not hardcoded to one asset.
        hx, hy = self._cursor_hotspot

        cursor_id = canvas.create_image(0, 0, image=self._cursor_image, anchor="nw")

        def draw_cursor(tip_x, tip_y):
            canvas.coords(cursor_id, tip_x - hx, tip_y - hy)

        pin_ids = [cursor_id]
        draw_cursor(start_x, start_y)

        # -- adaptive card: guess a side with room, then clamp for real --
        gap = 46
        dir_x = 1 if x < sw / 2 else -1
        dir_y = 1 if y < sh / 2 else -1
        est_w, est_h = 300, 78
        ax = x + gap if dir_x == 1 else x - gap - est_w
        ay = y + gap if dir_y == 1 else y - gap - est_h

        icon_size = 16
        guide_icon = self._icon(ICON_GUIDE_TARGET, icon_size, _ACCENT)
        icon_id = None
        caption_x = ax
        if guide_icon is not None:
            icon_id = canvas.create_image(ax, ay, anchor="nw", image=guide_icon)
            caption_x = ax + icon_size + 6

        caption_id = canvas.create_text(
            caption_x, ay, text="NEXT STEP", fill=_TEXT_FAINT, font=("Segoe UI", 8, "bold"), anchor="nw",
        )
        cap_bbox = canvas.bbox(caption_id)
        instr_id = canvas.create_text(
            ax, cap_bbox[3] + 5, text=instruction, fill=_TEXT, font=("Segoe UI", 13, "bold"),
            anchor="nw", width=300, justify="left",
        )
        instr_bbox = canvas.bbox(instr_id)
        label_id = canvas.create_text(
            ax, instr_bbox[3] + 4, text=label, fill=_TEXT_DIM, font=("Segoe UI", 9),
            anchor="nw", width=300, justify="left",
        )
        label_bbox = canvas.bbox(label_id)
        text_ids = [caption_id, instr_id, label_id] + ([icon_id] if icon_id is not None else [])

        pad = 14
        # Extra right-side room reserved for the close button (see
        # _draw_close_button) so it never overlaps the caption/instruction
        # text -- matters most for short instructions, where the "NEXT
        # STEP" caption + icon can end up the widest line in the card.
        close_clearance = 20
        bboxes = [cap_bbox, instr_bbox, label_bbox] + ([canvas.bbox(icon_id)] if icon_id is not None else [])
        bx1 = min(b[0] for b in bboxes) - pad
        by1 = min(b[1] for b in bboxes) - pad
        bx2 = max(b[2] for b in bboxes) + pad + close_clearance
        by2 = max(b[3] for b in bboxes) + pad

        margin = 20
        dx = dy = 0
        if bx1 < margin:
            dx = margin - bx1
        elif bx2 > sw - margin:
            dx = (sw - margin) - bx2
        if by1 < margin:
            dy = margin - by1
        elif by2 > sh - margin:
            dy = (sh - margin) - by2
        if dx or dy:
            for tid in text_ids:
                canvas.move(tid, dx, dy)
            bx1, by1, bx2, by2 = bx1 + dx, by1 + dy, bx2 + dx, by2 + dy

        # No connector line to the cursor pin — proximity plus the ghost
        # cursor's own glide there is enough; a line back to it was clutter.
        _draw_shadow(canvas, bx1, by1, bx2, by2, radius=16)
        card_id = _round_rect(canvas, bx1, by1, bx2, by2, radius=14, fill=_BG, outline=_BORDER)

        for tid in text_ids:
            canvas.tag_raise(tid, card_id)
        _draw_close_button(canvas, bx2, by1, on_close)
        for pid in pin_ids:
            canvas.tag_raise(pid)

        # Travel only — glide from the real cursor position to the target,
        # then stop dead. No idle bob/pulse once it lands: a real cursor
        # doesn't animate when it's just sitting still. Returned (not called)
        # so the caller can start it only once the window has actually faded
        # in — otherwise the glide finishes while still nearly invisible.
        travel_steps = max(1, config.SNAP_MS // 15)

        def travel(i=0):
            if not win.winfo_exists():
                return
            t = _ease_out_cubic(i / travel_steps)
            cur_x = start_x + (x - start_x) * t
            cur_y = start_y + (y - start_y) * t
            draw_cursor(cur_x, cur_y)
            if i < travel_steps:
                win.after(15, lambda: travel(i + 1))

        return travel

    def _render_bannerless_guide(self, canvas, win, sw, instruction, on_close):
        """No reliable on-screen location — just say what to do, centered
        near the top, with no arrow/reticle pointing at a guess.
        """
        card_w = 440
        ax = sw // 2 - card_w // 2 + 14
        ay = 90

        caption_id = canvas.create_text(
            ax, ay, text="NEXT STEP · NOT VISIBLE ON SCREEN", fill=_TEXT_FAINT,
            font=("Segoe UI", 8, "bold"), anchor="nw",
        )
        cap_bbox = canvas.bbox(caption_id)
        instr_id = canvas.create_text(
            ax, cap_bbox[3] + 3, text=instruction, fill=_TEXT, font=("Segoe UI", 13, "bold"),
            anchor="nw", width=card_w - 28, justify="left",
        )
        instr_bbox = canvas.bbox(instr_id)

        pad = 14
        close_clearance = 20  # see _render_pointing_guide — room for the close button
        bx1 = min(cap_bbox[0], instr_bbox[0]) - pad
        by1 = cap_bbox[1] - pad
        bx2 = max(cap_bbox[2], instr_bbox[2]) + pad + close_clearance
        by2 = instr_bbox[3] + pad

        _draw_shadow(canvas, bx1, by1, bx2, by2, radius=16)
        card_id = _round_rect(canvas, bx1, by1, bx2, by2, radius=14, fill=_BG, outline=_BORDER)
        canvas.tag_raise(caption_id, card_id)
        canvas.tag_raise(instr_id, card_id)
        _draw_close_button(canvas, bx2, by1, on_close)

    def _clear_guide_windows(self):
        for win in list(self._guide_windows):
            self._destroy_window(win, self._guide_windows)

    # -- offer (permission prompt) ------------------------------------------

    def ask_permission_sync(self, reason):
        """Blocks the calling thread until the user picks once/session/no."""
        result_q = queue.Queue(maxsize=1)

        def _do():
            win = tk.Toplevel(self._root)
            self._prompt_windows.append(win)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            w, h = 460, 186
            sw = win.winfo_screenwidth()
            win.geometry(f"{w}x{h}+{(sw - w) // 2}+40")
            win.config(bg=_TRANSPARENT_KEY)
            try:
                win.attributes("-transparentcolor", _TRANSPARENT_KEY)
            except tk.TclError:
                pass
            win.attributes("-alpha", 0.0)

            canvas = tk.Canvas(win, bg=_TRANSPARENT_KEY, highlightthickness=0, width=w, height=h)
            canvas.pack(fill="both", expand=True)
            _draw_shadow(canvas, 1, 1, w - 1, h - 1, radius=18)
            _round_rect(canvas, 1, 1, w - 1, h - 1, radius=18, fill=_BG, outline=_BORDER)

            canvas.create_text(
                22, 24, text="clicker wants to take this step for you", fill=_TEXT,
                font=("Segoe UI", 11, "bold"), anchor="nw", width=w - 44, justify="left",
            )
            canvas.create_text(
                22, 52, text=reason or "(no reason given)", fill=_TEXT_DIM,
                font=("Segoe UI", 10), anchor="nw", width=w - 44, justify="left",
            )

            def choose(scope):
                try:
                    result_q.put_nowait(scope)
                except queue.Full:
                    pass
                self._fade_out(win, on_done=lambda: self._destroy_window(win, self._prompt_windows))

            def make_btn(text, x_anchor, width, bg, fg, hover_bg, scope):
                btn = tk.Button(
                    win, text=text, font=("Segoe UI", 9, "bold"), bg=bg, fg=fg,
                    activebackground=hover_bg, activeforeground=fg, relief="flat", bd=0,
                    cursor="hand2", command=lambda: choose(scope),
                )
                self._add_hover(btn, bg, hover_bg)
                canvas.create_window(x_anchor, h - 28, window=btn, width=width, height=32)
                return btn

            # primary action emphasized (filled accent); the rest sit as
            # neutral gray buttons — no red, "No" reads as a plain option,
            # not a warning.
            make_btn("Just once", 92, 118, _ACCENT, "#ffffff", "#3d9bff", "once")
            make_btn("This session", w // 2, 126, _BG_SUBTLE, _TEXT, _BORDER, "session")
            make_btn("No", w - 66, 84, _BG_SUBTLE, _TEXT_DIM, _BORDER, "no")

            win.protocol("WM_DELETE_WINDOW", lambda: choose("no"))
            self._fade_in(win, on_done=lambda: win.focus_force())

        self.call(_do)
        return result_q.get()  # blocks the background thread, not the Tk thread

    # -- stop ------------------------------------------------------------------

    def clear_all(self):
        """Immediately tear down guide/prompt windows. Used by the stop hotkey.
        Deliberately not animated — stop must be instant. The HUD stays up.
        """
        def _do():
            for win in list(self._guide_windows):
                self._destroy_window(win, self._guide_windows)
            for win in list(self._prompt_windows):
                self._destroy_window(win, self._prompt_windows)
        self.call(_do)

    @staticmethod
    def _safe_destroy(win):
        try:
            win.destroy()
        except tk.TclError:
            pass

    @staticmethod
    def _destroy_window(win, tracking_list):
        if win in tracking_list:
            tracking_list.remove(win)
        job = getattr(win, "_pulse_job", None)
        if job is not None:
            try:
                win.after_cancel(job)
            except tk.TclError:
                pass
        try:
            win.destroy()
        except tk.TclError:
            pass
