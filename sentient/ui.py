"""Tk UI: Sidekick command bar, the "agent in control" border, Double Tap draft popup, morning cards.

All windows live on one Tk main thread; background threads post work with UI.call().
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

BG, FG, ACCENT, MUTED, CARD = "#15161a", "#f2f2f4", "#7c6cff", "#9a9aa6", "#1f2026"
FONT = ("Segoe UI", 11)


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
        return tk.Button(parent, text=text, command=cmd, font=FONT, relief="flat", padx=12, pady=4,
                         bg=ACCENT if primary else CARD, fg=FG, activebackground=ACCENT, activeforeground=FG, bd=0)

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
        win = self._window("Sidekick", 640, 110)
        win.overrideredirect(True)
        frame = tk.Frame(win, bg=BG, highlightbackground=ACCENT, highlightthickness=2)
        frame.pack(fill="both", expand=True)
        entry = tk.Entry(frame, font=("Segoe UI", 14), bg=BG, fg=FG, insertbackground=FG, relief="flat")
        entry.pack(fill="x", padx=16, pady=(16, 4))
        entry.insert(0, "")
        status = tk.Label(frame, text="Hand off a task - Enter to start, Esc to close", font=("Segoe UI", 9), bg=BG, fg=MUTED, anchor="w")
        status.pack(fill="x", padx=16)

        def submit(_=None):
            task = entry.get().strip()
            if task:
                entry.configure(state="disabled")
                status.configure(text="Working... (Ctrl+Alt+Esc or mouse to a screen corner stops it)")
                win.withdraw()  # get out of the way of the screenshots
                on_submit(task, lambda s: self.call(status.configure, {"text": s}), lambda: self.call(win.destroy))

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
