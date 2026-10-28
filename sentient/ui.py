"""Tk UI: Sidekick command bar, the "agent in control" border, Double Tap draft popup, morning cards.

All windows live on one Tk main thread; background threads post work with UI.call().
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

BG, FG, ACCENT, MUTED, CARD = "#0f1014", "#f4f4f7", "#8b7cff", "#9a9aa8", "#1c1d24"
HOVER, ACCENT_HOVER, LINE = "#2a2b34", "#a194ff", "#2c2d36"
FONT = ("Segoe UI Variable Text", 11)
TITLE = ("Segoe UI Variable Display Semibold", 16)
SMALL = ("Segoe UI Variable Text", 9)


class UI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Sentient")
        self._q: queue.Queue = queue.Queue()
        self.root.after(50, self._pump)
        self._border: list[tk.Toplevel] = []

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

    def _window(self, title: str, w: int, h: int, x=None, y=None) -> tk.Toplevel:
        win = tk.Toplevel(self.root, bg=BG)
        win.title(title)
        win.attributes("-topmost", True)
        sw = self.root.winfo_screenwidth()
        x = (sw - w) // 2 if x is None else x
        y = 80 if y is None else y
        win.geometry(f"{w}x{h}+{x}+{y}")
        return win

    def _button(self, parent, text, cmd, primary=False):
        bg, hover = (ACCENT, ACCENT_HOVER) if primary else (CARD, HOVER)
        b = tk.Button(parent, text=text, command=cmd, font=FONT, relief="flat", padx=14, pady=6, cursor="hand2",
                      bg=bg, fg="#0f1014" if primary else FG, activebackground=hover, activeforeground=FG, bd=0)
        b.bind("<Enter>", lambda _: b.configure(bg=hover))
        b.bind("<Leave>", lambda _: b.configure(bg=bg))
        return b

    # ---- Home window (tray click / Start Menu) ---------------------------
    def home(self, actions: list[tuple[str, str, object]], subtitle: str = ""):
        """actions: (title, hint, callback). One launcher window instead of hunting through the tray menu."""
        if getattr(self, "_home", None) and self._home.winfo_exists():
            self._home.deiconify()
            self._home.lift()
            self._home.focus_force()
            return
        win = self._window("Sentient", 440, 120 + 74 * len(actions))
        win.attributes("-topmost", False)
        self._home = win
        tk.Label(win, text="Sentient", font=TITLE, bg=BG, fg=FG).pack(anchor="w", padx=22, pady=(20, 0))
        tk.Label(win, text=subtitle, font=SMALL, bg=BG, fg=MUTED).pack(anchor="w", padx=22, pady=(0, 12))
        for title, hint, cb in actions:
            row = tk.Frame(win, bg=CARD, cursor="hand2", padx=16, pady=10)
            row.pack(fill="x", padx=18, pady=4)
            a = tk.Label(row, text=title, font=("Segoe UI Variable Text Semibold", 11), bg=CARD, fg=FG, anchor="w")
            a.pack(fill="x")
            b = tk.Label(row, text=hint, font=SMALL, bg=CARD, fg=MUTED, anchor="w")
            b.pack(fill="x")
            parts = (row, a, b)
            for w in parts:
                w.bind("<Button-1>", lambda _, cb=cb: cb())
                w.bind("<Enter>", lambda _, p=parts: [x.configure(bg=HOVER) for x in p])
                w.bind("<Leave>", lambda _, p=parts: [x.configure(bg=CARD) for x in p])

    # ---- Live status pill while Sidekick works ----------------------------
    def status_pill(self, on_stop):
        """Small bar at the bottom of the screen showing what Sidekick is doing, with a Stop button."""
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        win = self._window("Sentient status", 520, 46, (sw - 520) // 2, sh - 110)
        win.overrideredirect(True)
        frame = tk.Frame(win, bg=CARD, highlightbackground=ACCENT, highlightthickness=1)
        frame.pack(fill="both", expand=True)
        dot = tk.Label(frame, text="●", font=FONT, bg=CARD, fg=ACCENT)
        dot.pack(side="left", padx=(12, 6))
        label = tk.Label(frame, text="Starting...", font=SMALL, bg=CARD, fg=FG, anchor="w")
        label.pack(side="left", fill="x", expand=True)
        self._button(frame, "Stop", on_stop).pack(side="right", padx=6, pady=6)

        def pulse(on=[True]):
            if win.winfo_exists():
                on[0] = not on[0]
                dot.configure(fg=ACCENT if on[0] else CARD)
                win.after(600, pulse)

        pulse()
        return win, label

    def _ask(self, question, box, done):
        win = self._window("Sidekick needs you", 520, 220)
        tk.Label(win, text=question, wraplength=480, justify="left", font=FONT, bg=BG, fg=FG).pack(padx=20, pady=(18, 8), anchor="w")
        entry = tk.Entry(win, font=FONT, bg=CARD, fg=FG, insertbackground=FG, relief="flat")
        entry.pack(fill="x", padx=20, ipady=5)

        def finish(ans):
            box["answer"] = ans
            win.destroy()
            done.set()

        row = tk.Frame(win, bg=BG)
        row.pack(pady=14)
        self._button(row, "Approve", lambda: finish(entry.get() or "Yes, approved - go ahead."), True).pack(side="left", padx=4)
        self._button(row, "Deny", lambda: finish("No. Do not do that; stop and summarise.")).pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", lambda: finish("No answer - do not proceed."))
        entry.focus_force()

    # ---- Sidekick ----------------------------------------------------------
    def sidekick_bar(self, on_submit, on_stop):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        win = self._window("Sidekick", 680, 128, (sw - 680) // 2, sh // 3)
        win.overrideredirect(True)
        frame = tk.Frame(win, bg=BG, highlightbackground=ACCENT, highlightthickness=2)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="✦  What should I finish for you?", font=SMALL, bg=BG, fg=ACCENT, anchor="w").pack(fill="x", padx=18, pady=(14, 0))
        entry = tk.Entry(frame, font=("Segoe UI Variable Text", 15), bg=BG, fg=FG, insertbackground=ACCENT, relief="flat")
        entry.pack(fill="x", padx=18, pady=(6, 6))
        tk.Frame(frame, bg=LINE, height=1).pack(fill="x", padx=18)
        tk.Label(frame, text="Enter to start  ·  Esc to close  ·  Ctrl+Alt+Esc stops it anytime", font=SMALL, bg=BG, fg=MUTED, anchor="w").pack(fill="x", padx=18, pady=(6, 0))

        def submit(_=None):
            task = entry.get().strip()
            if task:
                win.destroy()  # get out of the way of the screenshots
                pill, label = self.status_pill(on_stop)
                on_submit(task, lambda s: self.call(label.configure, {"text": s[:80]}), lambda: self.call(pill.destroy))

        entry.bind("<Return>", submit)
        entry.bind("<Escape>", lambda _: (on_stop(), win.destroy()))
        entry.focus_force()

    def show_border(self, on: bool):
        for b in self._border:
            b.destroy()
        self._border = []
        if not on:
            return
        sw, sh, t = self.root.winfo_screenwidth(), self.root.winfo_screenheight(), 4
        for geo in (f"{sw}x{t}+0+0", f"{sw}x{t}+0+{sh - t}", f"{t}x{sh}+0+0", f"{t}x{sh}+{sw - t}+0"):
            b = tk.Toplevel(self.root, bg=ACCENT)
            b.overrideredirect(True)
            b.attributes("-topmost", True)
            b.geometry(geo)
            self._border.append(b)

    def toast(self, text: str, ms: int = 6000):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        win = self._window("Sentient", 420, 120, sw - 440, sh - 180)
        win.overrideredirect(True)
        tk.Label(win, text=text, wraplength=390, justify="left", font=FONT, bg=CARD, fg=FG, padx=14, pady=12).pack(fill="both", expand=True)
        win.bind("<Button-1>", lambda _: win.destroy())
        win.after(ms, win.destroy)

    # ---- Double Tap --------------------------------------------------------
    def draft_popup(self, draft: str, on_insert, on_redo):
        x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
        win = self._window("Double Tap", 560, 340, max(0, x - 280), max(0, y - 360))
        tk.Label(win, text="Draft - edit if you like", font=("Segoe UI", 9), bg=BG, fg=MUTED).pack(anchor="w", padx=14, pady=(10, 0))
        txt = tk.Text(win, wrap="word", font=FONT, bg=CARD, fg=FG, insertbackground=FG, relief="flat", height=10, padx=10, pady=8)
        txt.pack(fill="both", expand=True, padx=14, pady=6)
        txt.insert("1.0", draft)
        tweak = tk.Entry(win, font=("Segoe UI", 10), bg=CARD, fg=MUTED, insertbackground=FG, relief="flat")
        tweak.insert(0, "e.g. shorter, decline politely, suggest Thursday")
        tweak.bind("<FocusIn>", lambda _: (tweak.delete(0, "end"), tweak.configure(fg=FG)))
        tweak.pack(fill="x", padx=14, ipady=4)
        row = tk.Frame(win, bg=BG)
        row.pack(pady=10)

        def insert():
            text = txt.get("1.0", "end").strip()
            win.destroy()
            on_insert(text)

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(txt.get("1.0", "end").strip())
            win.destroy()

        def redo():
            hint = tweak.get().strip()
            win.destroy()
            on_redo("" if hint.startswith("e.g.") else hint)

        self._button(row, "Insert  ⏎", insert, True).pack(side="left", padx=4)
        self._button(row, "Copy", copy).pack(side="left", padx=4)
        self._button(row, "Redo", redo).pack(side="left", padx=4)
        self._button(row, "Close", win.destroy).pack(side="left", padx=4)
        win.bind("<Control-Return>", lambda _: insert())
        win.bind("<Escape>", lambda _: win.destroy())
        txt.focus_force()

    # ---- Proactive cards ---------------------------------------------------
    def cards_window(self, cards: list[dict], on_action, on_dismiss):
        win = self._window("Sentient - your morning", 620, 640)
        tk.Label(win, text="It did the catching up while you slept.", font=("Segoe UI Semibold", 15), bg=BG, fg=FG).pack(anchor="w", padx=18, pady=(16, 6))
        canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw", width=580)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(18, 0))
        sb.pack(side="right", fill="y")
        if not cards:
            tk.Label(inner, text="Nothing needs you right now.", font=FONT, bg=BG, fg=MUTED).pack(anchor="w", pady=20)
        for c in cards:
            f = tk.Frame(inner, bg=CARD, padx=14, pady=12)
            f.pack(fill="x", pady=6)
            tk.Label(f, text=c.get("tag", ""), font=("Segoe UI", 8, "bold"), bg=CARD, fg=ACCENT).pack(anchor="w")
            tk.Label(f, text=c["title"], font=("Segoe UI Semibold", 12), bg=CARD, fg=FG, wraplength=540, justify="left").pack(anchor="w", pady=(2, 4))
            tk.Label(f, text=c.get("body", ""), font=("Segoe UI", 10), bg=CARD, fg=MUTED, wraplength=540, justify="left").pack(anchor="w")
            row = tk.Frame(f, bg=CARD)
            row.pack(anchor="w", pady=(8, 0))
            if c.get("action") in ("reply", "sidekick"):
                self._button(row, c.get("action_label") or "Do it", lambda c=c, f=f: (f.destroy(), on_action(c)), True).pack(side="left", padx=(0, 6))
            if c.get("draft"):
                self._button(row, "Read the draft", lambda c=c: self._readonly(c["title"], c["draft"])).pack(side="left", padx=(0, 6))
            if c.get("task"):
                self._button(row, "Read the plan", lambda c=c: self._readonly(c["title"], c["task"])).pack(side="left", padx=(0, 6))
            self._button(row, "Dismiss", lambda c=c, f=f: (f.destroy(), on_dismiss(c))).pack(side="left")

    def _readonly(self, title: str, body: str):
        win = self._window(title, 520, 360)
        t = tk.Text(win, wrap="word", font=FONT, bg=CARD, fg=FG, relief="flat", padx=10, pady=8)
        t.insert("1.0", body)
        t.configure(state="disabled")
        t.pack(fill="both", expand=True, padx=12, pady=12)

    def run(self):
        self.root.mainloop()
