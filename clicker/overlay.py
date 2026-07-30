"""All on-screen UI: the persistent HUD dock (status/goal/mode/stop), the
spotlight goal-entry box, the animated guide marker (adaptive-placement card
+ connector line + corner-bracket reticle), and the animated permission
prompt (once / this session / no).

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

from . import config

_TRANSPARENT_KEY = "magenta"

_BG = "#1c1c1e"
_BORDER = "#2c2c2e"
_ACCENT_GUIDE = "#ff3b30"
_ACCENT_HUD = "#0a84ff"
_TEXT = "#ffffff"
_TEXT_DIM = "#c7c7cc"
_TEXT_FAINT = "#8e8e93"

STATUS_COLORS = {
    "watching": _ACCENT_HUD,
    "thinking": "#ffd60a",
    "guiding": _ACCENT_GUIDE,
    "paused": _TEXT_FAINT,
    "done": "#30d158",
}

HUD_W, HUD_H = 300, 92


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


def _closest_point_on_rect(x, y, bbox):
    x1, y1, x2, y2 = bbox
    return min(max(x, x1), x2), min(max(y, y1), y2)


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
        self._hud_accent_id = None
        self._hud_goal_text = None
        self._hud_mode_btn = None
        self._spotlight = None
        self._poll()

    # -- plumbing ----------------------------------------------------------

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

    def ensure_hud(self, on_toggle_mode, on_stop, on_edit_goal):
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
            _round_rect(canvas, 1, 1, HUD_W - 1, HUD_H - 1, radius=18, fill=_BG, outline=_BORDER)
            self._hud_canvas = canvas

            self._hud_accent_id = canvas.create_line(
                18, 4, HUD_W - 18, 4, fill=STATUS_COLORS["watching"], width=3, capstyle=tk.ROUND,
            )
            self._hud_status_text = canvas.create_text(
                36, 22, text="Watching", fill=_TEXT_DIM, font=("Segoe UI", 9), anchor="w",
            )
            self._hud_goal_text = canvas.create_text(
                16, 46, text="No goal set — click to add one", fill=_TEXT,
                font=("Segoe UI", 10, "bold"), anchor="w", width=268,
            )

            mode_btn = tk.Button(
                win, text="Guide", font=("Segoe UI", 9, "bold"), bg="#2c2c2e", fg=_TEXT,
                activebackground="#3a3a3c", activeforeground=_TEXT, relief="flat", bd=0,
                cursor="hand2", command=on_toggle_mode,
            )
            self._add_hover(mode_btn, "#2c2c2e", "#3a3a3c")
            canvas.create_window(16, 70, window=mode_btn, anchor="w", width=100, height=26)
            self._hud_mode_btn = mode_btn

            stop_btn = tk.Button(
                win, text="Stop", font=("Segoe UI", 9, "bold"), bg="#3a1414", fg="#ff453a",
                activebackground="#4a1818", activeforeground="#ff453a", relief="flat", bd=0,
                cursor="hand2", command=on_stop,
            )
            self._add_hover(stop_btn, "#3a1414", "#4a1818")
            canvas.create_window(HUD_W - 16, 70, window=stop_btn, anchor="e", width=70, height=26)

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
        c.itemconfig(self._hud_accent_id, fill=color)
        cx, cy = 22, 22

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
                self._hud_mode_btn.config(text=mode)

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
            _round_rect(canvas, 1, 1, w - 1, h - 1, radius=18, fill=_BG, outline=_ACCENT_HUD, width=2)

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
    # Design: a corner-bracket reticle (camera-focus style) snaps onto the
    # target, then breathes gently to hold attention. A card with the
    # instruction is placed on whichever side of the target has the most
    # screen room, then clamped fully on-screen if the estimate was off. A
    # dashed connector line is drawn from the reticle to the *closest point*
    # on the card's actual final bounding box, so it always looks correct
    # regardless of where the card ended up.

    def show_guide(self, instruction, x, y, label, seconds=None):
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

            final_half, arm = 26, 14
            bracket_ids = [
                canvas.create_line(0, 0, 0, 0, fill=_ACCENT_GUIDE, width=3, capstyle=tk.ROUND)
                for _ in range(8)
            ]

            # -- adaptive card: guess a side with room, then clamp for real --
            gap = 46
            dir_x = 1 if x < sw / 2 else -1
            dir_y = 1 if y < sh / 2 else -1
            est_w, est_h = 300, 78
            ax = x + gap if dir_x == 1 else x - gap - est_w
            ay = y + gap if dir_y == 1 else y - gap - est_h

            caption_id = canvas.create_text(
                ax, ay, text="NEXT STEP", fill=_ACCENT_GUIDE, font=("Segoe UI", 8, "bold"), anchor="nw",
            )
            cap_bbox = canvas.bbox(caption_id)
            instr_id = canvas.create_text(
                ax, cap_bbox[3] + 3, text=instruction, fill=_TEXT, font=("Segoe UI", 13, "bold"),
                anchor="nw", width=300, justify="left",
            )
            instr_bbox = canvas.bbox(instr_id)
            label_id = canvas.create_text(
                ax, instr_bbox[3] + 4, text=f"→ {label}", fill=_TEXT_DIM, font=("Segoe UI", 9),
                anchor="nw", width=300, justify="left",
            )
            label_bbox = canvas.bbox(label_id)
            text_ids = [caption_id, instr_id, label_id]

            pad = 14
            bx1 = min(cap_bbox[0], instr_bbox[0], label_bbox[0]) - pad
            by1 = cap_bbox[1] - pad
            bx2 = max(cap_bbox[2], instr_bbox[2], label_bbox[2]) + pad
            by2 = label_bbox[3] + pad

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

            glow_id = _round_rect(canvas, bx1 - 3, by1 - 3, bx2 + 3, by2 + 3,
                                   radius=17, fill="", outline="#3a1414", width=2)
            card_id = _round_rect(canvas, bx1, by1, bx2, by2, radius=14, fill=_BG, outline=_BORDER)

            cx, cy = _closest_point_on_rect(x, y, (bx1, by1, bx2, by2))
            ddx, ddy = cx - x, cy - y
            dist = math.hypot(ddx, ddy) or 1
            ux, uy = ddx / dist, ddy / dist
            lx, ly = x + ux * (final_half + 10), y + uy * (final_half + 10)
            line_id = canvas.create_line(lx, ly, cx, cy, fill=_ACCENT_GUIDE, width=2, dash=(5, 4))

            canvas.tag_lower(line_id)
            canvas.tag_raise(glow_id, line_id)
            canvas.tag_raise(card_id, glow_id)
            for tid in text_ids:
                canvas.tag_raise(tid, card_id)
            for bid in bracket_ids:
                canvas.tag_raise(bid)

            def bracket_coords(half):
                return [
                    (x - half, y - half, x - half + arm, y - half),
                    (x - half, y - half, x - half, y - half + arm),
                    (x + half, y - half, x + half - arm, y - half),
                    (x + half, y - half, x + half, y - half + arm),
                    (x - half, y + half, x - half + arm, y + half),
                    (x - half, y + half, x - half, y + half - arm),
                    (x + half, y + half, x + half - arm, y + half),
                    (x + half, y + half, x + half, y + half - arm),
                ]

            snap_steps = max(1, config.SNAP_MS // 15)

            def snap(i=0):
                if not win.winfo_exists():
                    return
                t = _ease_out_cubic(i / snap_steps)
                half = final_half + final_half * 0.9 * (1 - t)
                for bid, c in zip(bracket_ids, bracket_coords(half)):
                    canvas.coords(bid, *c)
                if i < snap_steps:
                    win.after(15, lambda: snap(i + 1))
                else:
                    breathe_start()

            def breathe_start():
                t0 = time.time()

                def breathe():
                    if not win.winfo_exists():
                        return
                    phase = ((time.time() - t0) * 1000 % config.PULSE_PERIOD_MS) / config.PULSE_PERIOD_MS
                    half = final_half + 3.5 * math.sin(phase * 2 * math.pi)
                    for bid, c in zip(bracket_ids, bracket_coords(half)):
                        canvas.coords(bid, *c)
                    win._pulse_job = win.after(40, breathe)

                breathe()

            def expire():
                if win.winfo_exists():
                    self._fade_out(win, on_done=lambda: self._destroy_window(win, self._guide_windows))

            win.after(int(seconds * 1000), expire)
            self._fade_in(win)
            snap()

        self.call(_do)

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
            _round_rect(canvas, 1, 1, w - 1, h - 1, radius=18, fill=_BG, outline=_ACCENT_GUIDE, width=2)
            canvas.create_line(18, 4, w - 18, 4, fill=_ACCENT_GUIDE, width=3, capstyle=tk.ROUND)

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

            # primary action emphasized (filled accent); secondary + destructive muted
            make_btn("Just once", 92, 118, _ACCENT_HUD, _TEXT, "#3d9bff", "once")
            make_btn("This session", w // 2, 126, "#2c2c2e", _TEXT, "#3a3a3c", "session")
            make_btn("No", w - 66, 84, "#3a1414", "#ff453a", "#4a1818", "no")

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
