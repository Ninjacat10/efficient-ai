"""The tray app: hotkeys, Double Tap listener, morning cards, Sidekick runs."""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
from pathlib import Path

import keyboard
import pystray
from . import brand, knowledge, proactive
from .config import CONFIG_PATH, KNOWLEDGE_DIR, load_config
from .doubletap import DoubleTap, DoubleTapListener
from .sidekick import Sidekick
from .ui import UI

log = logging.getLogger(__name__)
INSTANCE_PORT = 47823


class App:
    def __init__(self):
        self.cfg = load_config()
        self.ui = UI()
        self.ui.voice_on_open = self.cfg.get("voice_on_open", False)
        self.sidekick: Sidekick | None = None
        self.busy = threading.Lock()
        self.dt = DoubleTap()

    # ---- Sidekick ----------------------------------------------------------
    def open_sidekick(self, preset: str | None = None):
        if preset:
            def start():  # on the UI thread: show the status pill, then run
                pill, label = self.ui.status_pill(self.stop_sidekick)
                self._run_task(preset, lambda s: self.ui.call(label.configure, {"text": s[:90]}), lambda: self.ui.call(pill.destroy))

            self.ui.call(start)
        else:
            self.ui.call(self.ui.sidekick_bar, self._run_task, self.stop_sidekick)

    def _run_task(self, task, status, close):
        def work():
            if not self.busy.acquire(blocking=False):
                self.ui.call(self.ui.toast, "Sidekick is already working on something.")
                return
            try:
                self.ui.call(self.ui.show_border, True)
                self.sidekick = Sidekick(ask=self.ui.ask_blocking, status=status, pause=self.ui.pause_blocking)
                result = self.sidekick.run(task)
                try:
                    from . import memory

                    memory.log_activity("Sidekick", task, result)
                except Exception:
                    log.exception("couldn't write activity log")
            except Exception as e:
                log.exception("sidekick failed")
                result = f"Sidekick hit an error: {e}"
            finally:
                self.ui.call(self.ui.show_border, False)
                self.busy.release()
                close()
            self.ui.call(self.ui.toast, result, 12000)

        threading.Thread(target=work, daemon=True).start()

    def stop_sidekick(self):
        if self.sidekick:
            self.sidekick.stop()

    # ---- Double Tap --------------------------------------------------------
    def double_tap(self, extra: str = "", ctx=None):
        def work():
            try:
                c = ctx or self.dt.capture()
                draft = self.dt.draft(c, extra)
            except Exception as e:
                log.exception("double tap failed")
                self.ui.call(self.ui.toast, f"Double Tap failed: {e}")
                return
            self.ui.call(self.ui.draft_popup, draft,
                         lambda text: threading.Thread(target=self.dt.insert, args=(text,), daemon=True).start(),
                         lambda hint: self.double_tap(hint, c))

        threading.Thread(target=work, daemon=True).start()

    # ---- Proactive ---------------------------------------------------------
    def show_cards(self):
        cards = [c for c in proactive.load() if c["status"] == "new"]
        self.ui.call(self.ui.cards_window, cards, self._card_action, lambda c: proactive.set_status(c["id"], "dismissed"))

    def _card_action(self, card):
        proactive.set_status(card["id"], "done")
        if card["action"] == "reply":
            task = (f"{card['title']} Open the relevant conversation and paste this reply exactly, then ask me before sending:\n\n{card['draft']}")
        else:
            task = card["task"]
        self.open_sidekick(task)

    def refresh_now(self):
        def work():
            self.ui.call(self.ui.toast, "Reading what's new and updating your knowledge...")
            try:
                summary = knowledge.build()
                proactive.generate()
                self.ui.call(self.ui.toast, f"Knowledge updated from {summary['items']} new items.")
                self.show_cards()
            except Exception as e:
                log.exception("refresh failed")
                self.ui.call(self.ui.toast, f"Refresh failed: {e}")

        threading.Thread(target=work, daemon=True).start()

    # ---- Claude apps -------------------------------------------------------
    def open_claude_code(self):
        """A terminal running Claude Code (uses your Claude subscription) in the folder from config."""
        folder = os.path.expanduser(self.cfg.get("claude_code_folder", "~"))
        subprocess.Popen(["cmd", "/c", "start", "Claude Code", "cmd", "/k", "claude"], cwd=folder)

    def open_claude_desktop(self):
        """The Claude desktop app, where Cowork lives."""
        for p in (Path(os.environ.get("LOCALAPPDATA", "")) / "AnthropicClaude" / "claude.exe",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Claude" / "Claude.exe"):
            if p.exists():
                subprocess.Popen([str(p)])
                return
        try:
            os.startfile("claude://")
        except OSError:
            self.ui.call(self.ui.toast, "Couldn't find the Claude desktop app. Install it from claude.com/download.")

    def fill_form(self):
        """One key: fast-fill the job application (or any form) in the window you're looking at. Never submits."""
        from . import autofill

        def work():
            if not self.busy.acquire(blocking=False):
                self.ui.call(self.ui.toast, "Already working on something.")
                return
            holder = {}
            ready = threading.Event()

            def show():
                holder["pill"], holder["label"] = self.ui.status_pill(self.stop_sidekick)
                ready.set()

            self.ui.call(show)
            ready.wait(3)
            status = lambda s: "label" in holder and self.ui.call(holder["label"].configure, {"text": s[:90]})
            try:
                result = autofill.fill(status)
            except Exception as e:
                log.exception("autofill failed")
                result = f"Couldn't fill the form: {e}"
            finally:
                self.busy.release()
                if "pill" in holder:
                    self.ui.call(holder["pill"].destroy)
            self.ui.call(self.ui.toast, result, 15000)

        threading.Thread(target=work, daemon=True).start()

    def fill_form_sidekick(self):
        """Slower, screen-driving version: handles uploads, multi-page flows and odd widgets."""
        resume = self.cfg.get("resume_path", "")
        task = ("Fill out the form (usually a job application) in the window that is in front right now. Do not switch away from it.\n"
                "- Fill every field you can from the knowledge base and profile files: name, contact details, education, work history, "
                "skills, links, work authorization, availability, and short answers written in my voice.\n"
                + (f"- If it asks for a resume or CV, upload this file: {resume}\n" if resume else "")
                + "- Scroll through the whole page, and through every step of multi-page forms.\n"
                "- Collect everything you can't answer from what you know, and ask me ONCE with ask_user, as a short numbered list. "
                "Never guess at legal answers (sponsorship, demographics, criminal record) - ask or leave them.\n"
                "- Stop before the final Submit button and ask_user for approval to submit.")
        self.open_sidekick(task)

    def show_help(self):
        hk = self.cfg["hotkeys"]
        self.ui.call(self.ui.help_window, [
            ("Anywhere on your PC", [
                (hk.get("home", "ctrl+alt+e").title(), "Open the Efficient AI home window"),
                (hk["sidekick"].title(), "Sidekick: type or speak a task, and it does it on your screen"),
                (hk.get("fill_form", "ctrl+alt+f").title(), "Fill the job application or form in front of you"),
                (f"{hk['doubletap_key'].title()} ×2  or  {hk.get('reply', 'ctrl+alt+r').title()}", "In an email or chat: draft a reply in your voice"),
                (hk["kill_switch"].title(), "Stop Sidekick right now"),
                ("Mouse to a corner", "Emergency stop: slam the mouse into any screen corner"),
            ]),
            ("In the Sidekick box", [
                ("Enter", "Start the task"),
                ("Ctrl+M", "Speak instead of typing (you can type too)"),
                ("Esc", "Close"),
            ]),
            ("In the reply draft", [
                ("Ctrl+Enter", "Paste the reply where your cursor was"),
                ("Ctrl+R", "Rewrite it (type how first, e.g. \"shorter\")"),
                ("Ctrl+Shift+C", "Copy it"),
            ]),
            ("Everywhere else", [
                ("1 - 9", "Pick an option on the home window, or act on a morning card"),
                ("Enter / Esc", "Approve / deny when Sidekick asks before doing something"),
                ("Esc", "Close any Efficient AI window"),
            ]),
            ("What it can do", [
                ("Job applications", "Fills every field from your profile, uploads your resume, asks once for anything missing, waits for you before Submit"),
                ("Busywork", "\"Find the Stripe tab and finish the application\", \"book a study room\", \"rename these files\""),
                ("Replies", "Drafts email and chat replies that sound like you"),
                ("Mornings", "Cards with what needs you today, each one ready to act on"),
                ("Knowledge", "Learns from your mail, notes, files and second brain every night at 3 AM"),
            ]),
        ])

    def show_home(self):
        hk = self.cfg["hotkeys"]
        self.ui.call(self.ui.home, [
            ("Hand off a task", f"Type or speak it, Sidekick does it  ·  {hk['sidekick'].title()}", lambda: self.open_sidekick()),
            ("Fill this application", f"Fast: fills the form in front of you, never submits  ·  {hk.get('fill_form', 'ctrl+alt+f').title()}", self.fill_form),
            ("Fill with Sidekick", "Slower: also uploads your resume and clicks through pages", self.fill_form_sidekick),
            ("Morning cards", "What needs you today", self.show_cards),
            ("Refresh knowledge now", "Read new mail, notes and files", self.refresh_now),
            ("Claude Code", "Coding agent in a terminal, on your Claude plan", self.open_claude_code),
            ("Claude app / Cowork", "Open the Claude desktop app", self.open_claude_desktop),
            ("Knowledge folder", "Everything it knows about you, as files", lambda: os.startfile(KNOWLEDGE_DIR)),
            ("Settings", "config.json: providers, mail accounts, hotkeys", lambda: os.startfile(CONFIG_PATH)),
            ("Shortcuts & help", "Every shortcut and what it can do  ·  F1", self.show_help),
        ], f"{hk.get('home', 'ctrl+alt+e').title()} opens this  ·  double-tap {hk['doubletap_key'].title()} to draft a reply")

    def _listen_for_second_launch(self):
        """Opening Efficient AI again (Start Menu, desktop icon) shows the home window instead of a second copy."""
        def serve():
            while True:
                conn, _ = self._sock.accept()
                conn.close()
                self.show_home()

        threading.Thread(target=serve, daemon=True).start()

    # ---- wiring ------------------------------------------------------------
    def _watch_claude(self):
        """Learn from each Claude Code conversation shortly after it goes quiet - no waiting for 3 AM.

        The 5-minute check only looks at file timestamps on disk (free, no AI). The model is called once,
        after a session has gone quiet.
        """
        import time

        projects = Path.home() / ".claude" / "projects"
        seen = time.time()
        while True:
            time.sleep(300)
            try:
                latest = max((f.stat().st_mtime for f in projects.rglob("*.jsonl")), default=0)
                if latest > seen and time.time() - latest > 180:  # changed, and idle for 3 minutes
                    seen = latest
                    summary = knowledge.build(only=["ai_chats"])
                    log.info("learned from Claude sessions: %s", summary)
            except Exception:
                log.exception("Claude session learning failed")

    def run(self):
        self._sock = socket.socket()
        try:
            self._sock.bind(("127.0.0.1", INSTANCE_PORT))
        except OSError:  # already running: ask that copy to show itself
            with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=2):
                pass
            return
        self._sock.listen()
        self._listen_for_second_launch()

        hk = self.cfg["hotkeys"]
        keyboard.add_hotkey(hk["sidekick"], self.open_sidekick)
        if self.cfg["sources"].get("ai_chats", {}).get("claude_code", True):
            threading.Thread(target=self._watch_claude, daemon=True).start()
        keyboard.add_hotkey(hk["kill_switch"], self.stop_sidekick)
        keyboard.add_hotkey(hk.get("home", "ctrl+alt+e"), self.show_home)
        keyboard.add_hotkey(hk.get("fill_form", "ctrl+alt+f"), self.fill_form)
        keyboard.add_hotkey(hk.get("reply", "ctrl+alt+r"), lambda: self.double_tap())
        DoubleTapListener(self.double_tap, hk["doubletap_key"], hk["doubletap_window_ms"]).start()

        menu = pystray.Menu(
            pystray.MenuItem("Open Efficient AI", lambda: self.show_home(), default=True),
            pystray.MenuItem("Hand off a task (Sidekick)", lambda: self.open_sidekick()),
            pystray.MenuItem("Fill this application", lambda: self.fill_form()),
            pystray.MenuItem("Shortcuts & help", lambda: self.show_help()),
            pystray.MenuItem("Claude Code", lambda: self.open_claude_code()),
            pystray.MenuItem("Claude app / Cowork", lambda: self.open_claude_desktop()),
            pystray.MenuItem("Morning cards", lambda: self.show_cards()),
            pystray.MenuItem("Refresh knowledge now", lambda: self.refresh_now()),
            pystray.MenuItem("Open knowledge folder", lambda: os.startfile(KNOWLEDGE_DIR)),
            pystray.MenuItem("Settings (config.json)", lambda: os.startfile(CONFIG_PATH)),
            pystray.MenuItem("Stop Sidekick", lambda: self.stop_sidekick()),
            pystray.MenuItem("Quit", lambda icon: (icon.stop(), self.ui.call(self.ui.root.quit))),
        )
        tray = pystray.Icon("efficient", brand.logo(64), "Efficient AI", menu)
        tray.run_detached()

        if any(c["status"] == "new" for c in proactive.load()):
            self.ui.root.after(1500, self.show_cards)
        if self._needs_key():
            self.ui.call(self.ui.onboarding, self._save_key)
        else:
            self.show_home()
        self.ui.run()

    def _needs_key(self) -> bool:
        from .config import secret

        uses_gemini = any("gemini" in str(self.cfg.get(k, "")) for k in ("provider", "sidekick_provider", "fast_provider"))
        return uses_gemini and not (secret("GEMINI_API_KEY") or self.cfg.get("gemini_api_key"))

    def _save_key(self, key: str):
        from .config import save_secret

        save_secret("GEMINI_API_KEY", key)
        self.ui.call(self.ui.toast, "Connected. Efficient AI is ready - press Ctrl+Alt+E any time.")
        self.show_home()
