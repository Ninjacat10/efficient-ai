"""The tray app: hotkeys, Double Tap listener, morning cards, Sidekick runs."""
from __future__ import annotations

import logging
import os
import threading

import keyboard
import pystray
from PIL import Image, ImageDraw

from . import knowledge, proactive
from .config import CONFIG_PATH, KNOWLEDGE_DIR, load_config
from .doubletap import DoubleTap, DoubleTapListener
from .sidekick import Sidekick
from .ui import UI

log = logging.getLogger(__name__)


def _icon() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=(124, 108, 255, 255))
    d.ellipse((22, 22, 42, 42), fill=(21, 22, 26, 255))
    return img


class App:
    def __init__(self):
        self.cfg = load_config()
        self.ui = UI()
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

    # ---- wiring ------------------------------------------------------------
    def run(self):
        hk = self.cfg["hotkeys"]
        keyboard.add_hotkey(hk["sidekick"], self.open_sidekick)
        keyboard.add_hotkey(hk["kill_switch"], self.stop_sidekick)
        DoubleTapListener(self.double_tap, hk["doubletap_key"], hk["doubletap_window_ms"]).start()

        menu = pystray.Menu(
            pystray.MenuItem("Hand off a task (Sidekick)", lambda: self.open_sidekick(), default=True),
            pystray.MenuItem("Morning cards", lambda: self.show_cards()),
            pystray.MenuItem("Refresh knowledge now", lambda: self.refresh_now()),
            pystray.MenuItem("Open knowledge folder", lambda: os.startfile(KNOWLEDGE_DIR)),
            pystray.MenuItem("Settings (config.json)", lambda: os.startfile(CONFIG_PATH)),
            pystray.MenuItem("Stop Sidekick", lambda: self.stop_sidekick()),
            pystray.MenuItem("Quit", lambda icon: (icon.stop(), self.ui.call(self.ui.root.quit))),
        )
        tray = pystray.Icon("sentient", _icon(), "Sentient", menu)
        tray.run_detached()

        if any(c["status"] == "new" for c in proactive.load()):
            self.ui.root.after(1500, self.show_cards)
        self.ui.call(self.ui.toast, f"Sentient is running. {hk['sidekick'].title()} = Sidekick, double-tap {hk['doubletap_key'].title()} = reply.")
        self.ui.run()
