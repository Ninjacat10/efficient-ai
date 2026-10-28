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
            self._run_task(preset, lambda s: log.info(s), lambda: None)
        else:
            self.ui.call(self.ui.sidekick_bar, self._run_task, self.stop_sidekick)

    def _run_task(self, task, status, close):
        def work():
            if not self.busy.acquire(blocking=False):
                self.ui.call(self.ui.toast, "Sidekick is already working on something.")
                return
            try:
                self.ui.call(self.ui.show_border, True)
                self.sidekick = Sidekick(ask=self.ui.ask_blocking, status=status)
                result = self.sidekick.run(task)
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

    def show_home(self):
        hk = self.cfg["hotkeys"]
        self.ui.call(self.ui.home, [
            ("Hand off a task", f"Type or speak it, Sidekick does it  ·  {hk['sidekick'].title()}", lambda: self.open_sidekick()),
            ("Morning cards", "What needs you today", self.show_cards),
            ("Refresh knowledge now", "Read new mail, notes and files", self.refresh_now),
            ("Claude Code", "Coding agent in a terminal, on your Claude plan", self.open_claude_code),
            ("Claude app / Cowork", "Open the Claude desktop app", self.open_claude_desktop),
            ("Knowledge folder", "Everything it knows about you, as files", lambda: os.startfile(KNOWLEDGE_DIR)),
            ("Settings", "config.json: providers, mail accounts, hotkeys", lambda: os.startfile(CONFIG_PATH)),
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
        keyboard.add_hotkey(hk["kill_switch"], self.stop_sidekick)
        keyboard.add_hotkey(hk.get("home", "ctrl+alt+e"), self.show_home)
        DoubleTapListener(self.double_tap, hk["doubletap_key"], hk["doubletap_window_ms"]).start()

        menu = pystray.Menu(
            pystray.MenuItem("Open Efficient", lambda: self.show_home(), default=True),
            pystray.MenuItem("Hand off a task (Sidekick)", lambda: self.open_sidekick()),
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
        self.show_home()
        self.ui.run()
