"""Tk UI: home window, Sidekick command bar (type or speak), status pill, Double Tap popup, morning cards.

All windows live on one Tk main thread; background threads post work with UI.call().
Everything is keyboard-driven: every window lists its shortcuts in a footer, Esc always closes.
Windows 11 gets native rounded corners and dark title bars through DWM.
"""
from __future__ import annotations

import ctypes
import queue
import threading
import tkinter as tk
from tkinter import ttk

from PIL import ImageTk

from . import brand

# ---- design tokens -----------------------------------------------------------
BG = "#0d0e12"        # window
SURFACE = "#16171d"   # cards, inputs
RAISED = "#1f2029"    # hover
LINE = "#2a2b35"
FG = "#f5f5f8"
MUTED = "#8e8fa0"
ACCENT = "#7c5cff"
ACCENT_2 = "#3ba7ff"
ACCENT_HOVER = "#9278ff"
DANGER = "#ff5c7a"
F = "Segoe UI Variable Text"
FONT = (F, 11)
SMALL = (F, 9)
BOLD = ("Segoe UI Variable Text Semibold", 11)
TITLE = ("Segoe UI Variable Display Semibold", 17)
PAD = 20


def _dwm(win: tk.Misc) -> None:
    """Dark title bar + rounded corners on Windows 11 (silently ignored elsewhere)."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        for attr, val in ((20, 1), (33, 2)):  # DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_WINDOW_CORNER_PREFERENCE=ROUND
            v = ctypes.c_int(val)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass


def _foreground(win: tk.Misc, widget: tk.Misc | None = None) -> None:
    """Really bring a window to the front with keyboard focus.

    Windows refuses SetForegroundWindow from a background app (our windows open from a global hotkey),
    which left Esc and voice typing going to whatever app was in front. Attaching to the foreground
    thread's input queue is the standard way around that.
    """
    win.update_idletasks()
    try:
        u = ctypes.windll.user32
        hwnd = u.GetParent(win.winfo_id()) or win.winfo_id()
        fg_thread = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
        me = ctypes.windll.kernel32.GetCurrentThreadId()
        u.AttachThreadInput(fg_thread, me, True)
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        u.AttachThreadInput(fg_thread, me, False)
    except Exception:
        pass
    win.lift()
    (widget or win).focus_force()


def _kbd(parent, keys: str, bg=BG) -> tk.Label:
    """A small key-cap label, e.g. Ctrl+M."""
    return tk.Label(parent, text=keys, font=(F, 8), bg=RAISED if bg == BG else LINE, fg=FG, padx=6, pady=1)


class UI:
    def __init__(self):
        # Our own taskbar identity: without it Windows groups our windows under python.exe and shows its icon.
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("EfficientAI.App")
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Efficient AI")
        self._q: queue.Queue = queue.Queue()
        self.root.after(50, self._pump)
        self._border: list[tk.Toplevel] = []
        self._home: tk.Toplevel | None = None
        self._logo_small = ImageTk.PhotoImage(brand.logo(28), master=self.root)
        self._logo_big = ImageTk.PhotoImage(brand.logo(40), master=self.root)
        self._icon = ImageTk.PhotoImage(brand.logo(64), master=self.root)
        self.root.iconphoto(True, self._icon)
        self.voice_on_open = False

    # ---- thread plumbing ---------------------------------------------------
    def call(self, fn, *args):
        self._q.put((fn, args))

    def _pump(self):
        try:
            while True:
                fn, args = self._q.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.root.after(50, self._pump)

    def ask_blocking(self, question: str) -> str:
        """Called from a worker thread: show an approval dialog and wait for the answer."""
        done, box = threading.Event(), {"answer": ""}
        self.call(self._ask, question, box, done)
        done.wait()
        return box["answer"]

    # ---- building blocks ---------------------------------------------------
    def _window(self, title: str, w: int, h: int | None = None, x=None, y=None, chrome=True) -> tk.Toplevel:
        win = tk.Toplevel(self.root, bg=BG)
        win.title(title)
        win.attributes("-topmost", True)
        if not chrome:
            win.overrideredirect(True)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = (sw - w) // 2 if x is None else x
        y = sh // 6 if y is None else y
        win.geometry(f"{w}x{h}+{x}+{y}" if h else f"{w}x1+{x}+{y}")
        win.bind("<Escape>", lambda _: win.destroy())
        _dwm(win)
        return win

    @staticmethod
    def _fit(win: tk.Toplevel, w: int):
        """Size the window to its content so nothing gets clipped or overlaps."""
        win.update_idletasks()
        win.geometry(f"{w}x{win.winfo_reqheight()}")

    def _button(self, parent, text, cmd, primary=False, bg=BG):
        base, hover = (ACCENT, ACCENT_HOVER) if primary else (SURFACE if bg == BG else RAISED, RAISED if bg == BG else LINE)
        b = tk.Label(parent, text=text, font=BOLD if primary else FONT, bg=base, fg=FG, padx=16, pady=7, cursor="hand2")
        b.bind("<Button-1>", lambda _: cmd())
        b.bind("<Enter>", lambda _: b.configure(bg=hover))
        b.bind("<Leave>", lambda _: b.configure(bg=base))
        return b

    def _header(self, win, title: str, subtitle: str = ""):
        head = tk.Frame(win, bg=BG)
        head.pack(fill="x", padx=PAD, pady=(PAD, 12))
        tk.Label(head, image=self._logo_big, bg=BG).pack(side="left")
        text = tk.Frame(head, bg=BG)
        text.pack(side="left", padx=12, fill="x", expand=True)
        tk.Label(text, text=title, font=TITLE, bg=BG, fg=FG, anchor="w").pack(fill="x")
        if subtitle:
            width = int(win.geometry().split("x")[0]) - 2 * PAD - 70
            tk.Label(text, text=subtitle, font=SMALL, bg=BG, fg=MUTED, anchor="w", justify="left", wraplength=width).pack(fill="x")

    def _footer(self, win, shortcuts: list[tuple[str, str]]):
        tk.Frame(win, bg=LINE, height=1).pack(fill="x", padx=PAD, pady=(12, 0))
        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=PAD, pady=(10, PAD - 4))
        for keys, what in shortcuts:
            _kbd(foot, keys).pack(side="left")
            tk.Label(foot, text=what, font=SMALL, bg=BG, fg=MUTED).pack(side="left", padx=(5, 14))

    # ---- home window ---------------------------------------------------------
    def home(self, actions: list[tuple[str, str, object]], subtitle: str = ""):
        """actions: (title, hint, callback). Press 1-9 to pick one without the mouse."""
        if self._home is not None and self._home.winfo_exists():
            self._home.deiconify()
            self._home.lift()
            _foreground(self._home)
            return
        W = 470
        win = self._window("Efficient AI", W, chrome=True)
        win.attributes("-topmost", False)
        self._home = win
        self._header(win, "Efficient AI", subtitle)
        # Scrollable list: it grows with the menu but never runs off a small laptop screen.
        box = tk.Frame(win, bg=BG)
        box.pack(fill="both", expand=True, padx=PAD - 4)
        canvas = tk.Canvas(box, bg=BG, highlightthickness=0, width=W - 2 * PAD)
        style = ttk.Style(win)
        style.theme_use("clam")  # the only built-in theme whose scrollbar can be recolored to match
        style.configure("Efficient.Vertical.TScrollbar", background=RAISED, troughcolor=BG, bordercolor=BG,
                        lightcolor=RAISED, darkcolor=RAISED, arrowcolor=MUTED, gripcount=0)
        bar = ttk.Scrollbar(box, orient="vertical", command=canvas.yview, style="Efficient.Vertical.TScrollbar")
        rows = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=rows, anchor="nw", width=W - 2 * PAD - 12)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-e.delta // 120, "units"))
        win.bind("<Down>", lambda _: canvas.yview_scroll(1, "units"))
        win.bind("<Up>", lambda _: canvas.yview_scroll(-1, "units"))

        def run(cb):
            win.withdraw()
            cb()

        for n, (title, hint, cb) in enumerate(actions, 1):
            row = tk.Frame(rows, bg=BG, cursor="hand2", padx=10, pady=8)
            row.pack(fill="x", pady=1)
            num = tk.Label(row, text=str(n), font=(F, 9), bg=SURFACE, fg=MUTED, width=2, pady=2)
            num.pack(side="left", padx=(0, 12))
            col = tk.Frame(row, bg=BG)
            col.pack(side="left", fill="x", expand=True)
            a = tk.Label(col, text=title, font=BOLD, bg=BG, fg=FG, anchor="w")
            a.pack(fill="x")
            b = tk.Label(col, text=hint, font=SMALL, bg=BG, fg=MUTED, anchor="w", wraplength=W - 110, justify="left")
            b.pack(fill="x")
            parts = (row, col, a, b)
            for w in parts + (num,):
                w.bind("<Button-1>", lambda _, cb=cb: run(cb))
            for w in parts + (num,):
                w.bind("<Enter>", lambda _, p=parts: [x.configure(bg=RAISED) for x in p])
                w.bind("<Leave>", lambda _, p=parts: [x.configure(bg=BG) for x in p])
            if n <= 9:
                win.bind(str(n), lambda _, cb=cb: run(cb))
            if title.startswith("Shortcuts"):
                win.bind("<F1>", lambda _, cb=cb: run(cb))
        self._footer(win, [("1-9", "open"), ("↑ ↓", "scroll"), ("Esc", "hide")])
        win.bind("<Escape>", lambda _: win.withdraw())
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        win.update_idletasks()
        full = rows.winfo_reqheight()
        max_list = int(self.root.winfo_screenheight() * 0.45)  # leaves room for header, footer and taskbar
        canvas.configure(height=min(full, max_list), scrollregion=(0, 0, 0, full))
        if full > max_list:
            bar.pack(side="right", fill="y")
        self._fit(win, W)
        _foreground(win)

    # ---- first run: connect an AI ------------------------------------------------
    def onboarding(self, on_save):
        """Shown once when no AI key is set. The user pastes their own free Gemini key."""
        import webbrowser

        W = 520
        win = self._window("Welcome to Efficient AI", W)
        self._header(win, "Welcome to Efficient AI", "One step: connect a free AI key so it can think.")
        steps = ("1.  Click \"Get a free key\" and sign in with Google.\n"
                 "2.  Click \"Create API key\" and copy it.\n"
                 "3.  Paste it below and press Enter.")
        tk.Label(win, text=steps, font=FONT, bg=BG, fg=FG, justify="left", anchor="w").pack(fill="x", padx=PAD)
        entry = tk.Entry(win, font=FONT, bg=SURFACE, fg=FG, insertbackground=ACCENT, relief="flat", show="•",
                         highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
        entry.pack(fill="x", padx=PAD, pady=(14, 0), ipady=7)
        note = tk.Label(win, text="Stored only on this PC, for your Windows account.", font=SMALL, bg=BG, fg=MUTED, anchor="w")
        note.pack(fill="x", padx=PAD, pady=(6, 0))

        def save(_=None):
            key = entry.get().strip()
            if len(key) < 20:
                note.configure(text="That doesn't look like a full key - paste the whole thing.", fg=DANGER)
                return
            win.destroy()
            on_save(key)

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=PAD, pady=(14, 0))
        self._button(row, "Get a free key", lambda: webbrowser.open("https://aistudio.google.com/apikey"), True).pack(side="left")
        self._button(row, "Save", save).pack(side="left", padx=8)
        self._footer(win, [("Enter", "save"), ("Esc", "later")])
        entry.bind("<Return>", save)
        self._fit(win, W)
        _foreground(win, entry)

    # ---- paused: continue or end ------------------------------------------------
    def pause_blocking(self) -> bool:
        """Called from a worker thread when you grab the mouse. True = continue the task."""
        done, box = threading.Event(), {"go": False}
        self.call(self._pause, box, done)
        done.wait()
        return box["go"]

    def _pause(self, box, done):
        W = 460
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        win = self._window("Sidekick paused", W, x=sw - W - 24, y=sh - 260)
        self._header(win, "Paused", "You took the mouse. Do what you need, then pick up where it left off.")

        def finish(go):
            if not done.is_set():
                box["go"] = go
                win.destroy()
                done.set()

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=PAD)
        self._button(row, "Continue task", lambda: finish(True), True).pack(side="left")
        self._button(row, "End task", lambda: finish(False)).pack(side="left", padx=8)
        self._footer(win, [("Enter", "continue"), ("Esc", "end task")])
        win.bind("<Return>", lambda _: finish(True))
        win.bind("<Escape>", lambda _: finish(False))
        win.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        self._fit(win, W)
        win.geometry(f"+{sw - W - 24}+{sh - win.winfo_reqheight() - 140}")  # above the status bar, fully on screen
        # Don't steal focus - you're busy with the mouse. It stays on top until you choose.

    # ---- shortcuts & help ------------------------------------------------------
    def help_window(self, sections: list[tuple[str, list[tuple[str, str]]]]):
        """A cheat sheet: every shortcut and what Efficient AI can do."""
        W = 640
        win = self._window("Efficient AI - shortcuts & help", W, 700)
        self._header(win, "Shortcuts & help", "Everything works from the keyboard. Press F1 on the home window to come back here.")
        canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw", width=W - 2 * PAD)
        self._footer(win, [("↑ ↓", "scroll"), ("Esc", "close")])
        canvas.pack(fill="both", expand=True, padx=PAD, before=win.pack_slaves()[-2])
        win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-e.delta // 120, "units"))
        win.bind("<Down>", lambda _: canvas.yview_scroll(2, "units"))
        win.bind("<Up>", lambda _: canvas.yview_scroll(-2, "units"))
        for title, rows in sections:
            tk.Label(inner, text=title.upper(), font=(F, 8, "bold"), bg=BG, fg=ACCENT_2, anchor="w").pack(fill="x", pady=(14, 6))
            box = tk.Frame(inner, bg=SURFACE, padx=14, pady=6)
            box.pack(fill="x")
            box.columnconfigure(1, weight=1)
            for r, (keys, what) in enumerate(rows):
                tk.Label(box, text=keys, font=(F, 9), bg=LINE, fg=FG, padx=7, pady=2).grid(row=r, column=0, sticky="nw", pady=6)
                tk.Label(box, text=what, font=SMALL, bg=SURFACE, fg=FG, anchor="w", justify="left",
                         wraplength=W - 2 * PAD - 230).grid(row=r, column=1, sticky="w", padx=(14, 0), pady=6)
            box.columnconfigure(0, minsize=170)
        _foreground(win)

    # ---- approvals -----------------------------------------------------------
    def _ask(self, question, box, done):
        W = 520
        win = self._window("Efficient AI needs you", W)
        self._header(win, "Quick check", "Sidekick paused before doing this:")
        tk.Label(win, text=question, wraplength=W - 2 * PAD, justify="left", font=FONT, bg=BG, fg=FG, anchor="w").pack(fill="x", padx=PAD)
        entry = tk.Entry(win, font=FONT, bg=SURFACE, fg=FG, insertbackground=ACCENT, relief="flat", highlightthickness=1,
                         highlightbackground=LINE, highlightcolor=ACCENT)
        entry.pack(fill="x", padx=PAD, pady=(14, 0), ipady=7)

        def finish(ans):
            if not done.is_set():
                box["answer"] = ans
                win.destroy()
                done.set()

        approve = lambda: finish(entry.get() or "Yes, approved - go ahead.")
        deny = lambda: finish("No. Do not do that; stop and summarise.")
        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=PAD, pady=(14, 0))
        self._button(row, "Approve", approve, True).pack(side="left")
        self._button(row, "Deny", deny).pack(side="left", padx=8)
        self._footer(win, [("Enter", "approve"), ("Esc", "deny"), ("Win+H", "speak an answer")])
        win.bind("<Return>", lambda _: approve())
        win.bind("<Escape>", lambda _: deny())
        win.protocol("WM_DELETE_WINDOW", lambda: finish("No answer - do not proceed."))
        self._fit(win, W)
        _foreground(win, entry)

    # ---- Sidekick command bar: type and speak --------------------------------
    def sidekick_bar(self, on_submit, on_stop):
        W = 700
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        win = self._window("Sidekick", W, x=(sw - W) // 2, y=sh // 4, chrome=False)
        frame = tk.Frame(win, bg=BG, highlightbackground=ACCENT, highlightthickness=1)
        frame.pack(fill="both", expand=True)
        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", padx=18, pady=(16, 0))
        tk.Label(top, image=self._logo_small, bg=BG).pack(side="left")
        entry = tk.Entry(top, font=(F, 15), bg=BG, fg=FG, insertbackground=ACCENT, relief="flat")
        entry.pack(side="left", fill="x", expand=True, padx=12, ipady=4)
        mic = tk.Label(top, text="🎙", font=(F, 14), bg=SURFACE, fg=MUTED, padx=8, cursor="hand2")
        mic.pack(side="right")
        tk.Frame(frame, bg=LINE, height=1).pack(fill="x", padx=18, pady=(12, 0))
        foot = tk.Frame(frame, bg=BG)
        foot.pack(fill="x", padx=18, pady=(10, 14))
        for keys, what in (("Enter", "start"), ("Ctrl+M", "speak"), ("Esc", "close"), ("Ctrl+Alt+Esc", "stop anytime")):
            _kbd(foot, keys).pack(side="left")
            tk.Label(foot, text=what, font=SMALL, bg=BG, fg=MUTED).pack(side="left", padx=(5, 12))

        import keyboard

        def close(_=None):
            try:
                keyboard.remove_hotkey(esc_hook)
            except (KeyError, ValueError):
                pass
            if win.winfo_exists():
                win.destroy()

        # Esc via a global hook as well, so it works even if Windows kept focus on another app.
        esc_hook = keyboard.add_hotkey("esc", lambda: self.call(close))

        def voice(_=None):
            # Windows voice typing (Win+H) dictates straight into the focused box, and you can keep typing.
            _foreground(win, entry)
            mic.configure(fg=ACCENT, bg=RAISED)
            threading.Thread(target=_press_win_h, daemon=True).start()
            return "break"

        def submit(_=None):
            task = entry.get().strip()
            if task:
                close()  # get out of the way of the screenshots
                pill, label = self.status_pill(on_stop)
                on_submit(task, lambda s: self.call(label.configure, {"text": s[:90]}), lambda: self.call(pill.destroy))

        mic.bind("<Button-1>", voice)
        entry.bind("<Return>", submit)
        entry.bind("<Control-m>", voice)
        entry.bind("<F2>", voice)
        win.bind("<Escape>", close)
        self._fit(win, W)
        _foreground(win, entry)
        win.after(150, lambda: _foreground(win, entry))  # once more after Windows finishes showing it
        if self.voice_on_open:
            win.after(300, voice)

    # ---- live status pill while Sidekick works -------------------------------
    def status_pill(self, on_stop):
        W = 760
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        win = self._window("Efficient AI status", W, x=(sw - W) // 2, y=sh - 120, chrome=False)
        frame = tk.Frame(win, bg=SURFACE, highlightbackground=LINE, highlightthickness=1)
        frame.pack(fill="both", expand=True)
        dot = tk.Label(frame, text="●", font=FONT, bg=SURFACE, fg=ACCENT)
        dot.pack(side="left", padx=(14, 8), pady=10)
        label = tk.Label(frame, text="Starting...", font=SMALL, bg=SURFACE, fg=FG, anchor="w")
        label.pack(side="left", fill="x", expand=True)
        stop = self._button(frame, "Stop", on_stop, bg=SURFACE)
        stop.pack(side="right", padx=6, pady=6)
        _kbd(frame, "Ctrl+Alt+Esc", bg=SURFACE).pack(side="right", padx=(0, 8))
        tk.Label(frame, text="Move mouse = pause  ·  stop:", font=SMALL, bg=SURFACE, fg=MUTED).pack(side="right", padx=(0, 6))

        def pulse(on=[True]):
            if win.winfo_exists():
                on[0] = not on[0]
                dot.configure(fg=ACCENT if on[0] else ACCENT_2)
                win.after(600, pulse)

        pulse()
        self._fit(win, W)
        return win, label

    def show_border(self, on: bool):
        for b in self._border:
            b.destroy()
        self._border = []
        if not on:
            return
        sw, sh, t = self.root.winfo_screenwidth(), self.root.winfo_screenheight(), 3
        for geo in (f"{sw}x{t}+0+0", f"{sw}x{t}+0+{sh - t}", f"{t}x{sh}+0+0", f"{t}x{sh}+{sw - t}+0"):
            b = tk.Toplevel(self.root, bg=ACCENT)
            b.overrideredirect(True)
            b.attributes("-topmost", True)
            b.geometry(geo)
            self._border.append(b)

    def toast(self, text: str, ms: int = 6000):
        W = 400
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        win = self._window("Efficient AI", W, x=sw - W - 24, y=sh - 200, chrome=False)
        frame = tk.Frame(win, bg=SURFACE, highlightbackground=LINE, highlightthickness=1)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, image=self._logo_small, bg=SURFACE).pack(side="left", anchor="n", padx=(14, 0), pady=14)
        tk.Label(frame, text=text, wraplength=W - 80, justify="left", font=FONT, bg=SURFACE, fg=FG, anchor="w").pack(
            side="left", fill="x", expand=True, padx=12, pady=14)
        for w in (win, frame):
            w.bind("<Button-1>", lambda _: win.destroy())
        self._fit(win, W)
        win.geometry(f"+{sw - W - 24}+{sh - win.winfo_reqheight() - 90}")
        win.after(ms, lambda: win.winfo_exists() and win.destroy())

    # ---- Double Tap ------------------------------------------------------------
    def draft_popup(self, draft: str, on_insert, on_redo):
        W = 580
        x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
        win = self._window("Double Tap", W, x=max(0, x - W // 2), y=max(0, y - 420))
        self._header(win, "Your reply", "Edit freely - it's written in your voice")
        txt = tk.Text(win, wrap="word", font=FONT, bg=SURFACE, fg=FG, insertbackground=ACCENT, relief="flat", height=9,
                      padx=12, pady=10, highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
        txt.pack(fill="x", padx=PAD)
        txt.insert("1.0", draft)
        tweak = tk.Entry(win, font=FONT, bg=SURFACE, fg=MUTED, insertbackground=ACCENT, relief="flat",
                         highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
        hint = "How should it change? e.g. shorter, decline politely"
        tweak.insert(0, hint)
        tweak.bind("<FocusIn>", lambda _: tweak.get() == hint and (tweak.delete(0, "end"), tweak.configure(fg=FG)))
        tweak.pack(fill="x", padx=PAD, pady=(10, 0), ipady=6)

        def insert():
            text = txt.get("1.0", "end").strip()
            win.destroy()
            on_insert(text)

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(txt.get("1.0", "end").strip())
            win.destroy()

        def redo():
            h = tweak.get().strip()
            win.destroy()
            on_redo("" if h == hint else h)

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=PAD, pady=(14, 0))
        self._button(row, "Insert", insert, True).pack(side="left")
        self._button(row, "Copy", copy).pack(side="left", padx=8)
        self._button(row, "Rewrite", redo).pack(side="left")
        self._footer(win, [("Ctrl+Enter", "insert"), ("Ctrl+R", "rewrite"), ("Ctrl+Shift+C", "copy"), ("Esc", "close")])
        win.bind("<Control-Return>", lambda _: insert())
        win.bind("<Control-r>", lambda _: redo())
        win.bind("<Control-Shift-C>", lambda _: copy())
        tweak.bind("<Return>", lambda _: redo())
        self._fit(win, W)
        _foreground(win, txt)

    # ---- proactive cards ----------------------------------------------------------
    def cards_window(self, cards: list[dict], on_action, on_dismiss):
        W = 640
        win = self._window("Efficient AI - your morning", W, 660)
        self._header(win, "Good morning", "Caught up while you slept. Press a number to act on a card.")
        canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw", width=W - 2 * PAD)
        canvas.pack(fill="both", expand=True, padx=PAD)
        canvas.bind_all("<MouseWheel>", lambda e: canvas.winfo_exists() and canvas.yview_scroll(-e.delta // 120, "units"))
        if not cards:
            tk.Label(inner, text="Nothing needs you right now.", font=FONT, bg=BG, fg=MUTED).pack(anchor="w", pady=20)
        wrap = W - 2 * PAD - 36
        for n, c in enumerate(cards, 1):
            f = tk.Frame(inner, bg=SURFACE, padx=16, pady=14)
            f.pack(fill="x", pady=5)
            top = tk.Frame(f, bg=SURFACE)
            top.pack(fill="x")
            _kbd(top, str(n), bg=SURFACE).pack(side="left")
            tk.Label(top, text=(c.get("tag") or "").upper(), font=(F, 8, "bold"), bg=SURFACE, fg=ACCENT_2).pack(side="left", padx=8)
            tk.Label(f, text=c["title"], font=BOLD, bg=SURFACE, fg=FG, wraplength=wrap, justify="left", anchor="w").pack(fill="x", pady=(6, 2))
            tk.Label(f, text=c.get("body", ""), font=SMALL, bg=SURFACE, fg=MUTED, wraplength=wrap, justify="left", anchor="w").pack(fill="x")
            row = tk.Frame(f, bg=SURFACE)
            row.pack(anchor="w", pady=(10, 0))
            act = None
            if c.get("action") in ("reply", "sidekick"):
                act = lambda c=c, f=f: (f.destroy(), on_action(c))
                self._button(row, c.get("action_label") or "Do it", act, True, bg=SURFACE).pack(side="left", padx=(0, 6))
            if c.get("draft"):
                self._button(row, "Draft", lambda c=c: self._readonly(c["title"], c["draft"]), bg=SURFACE).pack(side="left", padx=(0, 6))
            if c.get("task"):
                self._button(row, "Plan", lambda c=c: self._readonly(c["title"], c["task"]), bg=SURFACE).pack(side="left", padx=(0, 6))
            self._button(row, "Dismiss", lambda c=c, f=f: (f.destroy(), on_dismiss(c)), bg=SURFACE).pack(side="left")
            if act and n <= 9:
                win.bind(str(n), lambda _, act=act: act())
        self._footer(win, [("1-9", "do it"), ("Esc", "close")])
        _foreground(win)

    def _readonly(self, title: str, body: str):
        W = 540
        win = self._window(title, W, 400)
        t = tk.Text(win, wrap="word", font=FONT, bg=SURFACE, fg=FG, relief="flat", padx=14, pady=12)
        t.insert("1.0", body)
        t.configure(state="disabled")
        t.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        _foreground(win)

    def run(self):
        self.root.mainloop()


def _press_win_h():
    """Open Windows voice typing. Built into Windows 11, free, and types wherever the cursor is."""
    import time

    import keyboard

    time.sleep(0.05)
    for k in ("ctrl", "alt", "shift"):  # still held from the hotkey that opened the box, which breaks Win+H
        keyboard.release(k)
    keyboard.send("windows+h")
