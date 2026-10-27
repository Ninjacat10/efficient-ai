"""Double Tap - tap Right Ctrl twice and get the reply you would have written.

Reads the focused window (UI Automation text + a screenshot), combines it with your
writing style and knowledge, drafts a reply, and pastes it back where your cursor was
once you approve it.
"""
from __future__ import annotations

import ctypes
import logging
import time

import keyboard
import pyautogui
import pyperclip

from .config import load_config
from .knowledge import knowledge_context, read_topic
from .llm import get_provider
from .screen import Screen

log = logging.getLogger(__name__)

SYSTEM = """You write replies as the user, in their voice. You know them well from their knowledge base.
Match their writing style exactly (length, tone, greeting, sign-off, punctuation, emoji habits) for this audience.
Use concrete facts from the knowledge base (availability, prior commitments, details) when relevant; never invent facts.
Output only the reply text that should be pasted - no quotes, no preamble, no subject line unless the app clearly needs one.

<writing_style>
{style}
</writing_style>

<knowledge>
{knowledge}
</knowledge>"""

PROMPT = """The user double-tapped while in this window: "{title}".

Text visible in the window (via accessibility):
<window_text>
{text}
</window_text>

A screenshot of the screen is attached. Work out what conversation or message the user is replying to and draft their reply.{extra}"""


def _foreground():
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return hwnd, buf.value


def _window_text(max_chars: int = 12_000) -> str:
    """Pull text out of the focused window's accessibility tree (works for Outlook, Teams, browsers, WhatsApp...)."""
    try:
        import uiautomation as auto

        win = auto.GetForegroundControl()
        out, n = [], 0
        for ctrl, _depth in auto.WalkControl(win, maxDepth=25):
            name = (ctrl.Name or "").strip()
            if name and len(name) > 1:
                out.append(name)
                n += len(name)
            if n > max_chars * 2:
                break
        seen, dedup = set(), []
        for line in out:
            if line not in seen:
                seen.add(line)
                dedup.append(line)
        return "\n".join(dedup)[-max_chars:]
    except Exception as e:
        log.debug("UIA read failed: %s", e)
        return ""


class DoubleTap:
    def __init__(self):
        self.cfg = load_config()
        self.screen = Screen()
        self.target_hwnd = None

    def capture(self) -> dict:
        hwnd, title = _foreground()
        self.target_hwnd = hwnd
        return {"title": title, "text": _window_text(), "image": self.screen.grab()}

    def draft(self, ctx: dict, extra: str = "") -> str:
        llm = get_provider(self.cfg["doubletap_provider"])
        system = SYSTEM.format(style=read_topic("writingstyle") or "(unknown yet - write naturally and briefly)", knowledge=knowledge_context(20_000))
        extra = f"\n\nAdditional instruction from the user: {extra}" if extra else ""
        return llm.chat(system, PROMPT.format(title=ctx["title"], text=ctx["text"], extra=extra), images=[ctx["image"]], effort="low", max_tokens=4000)

    def insert(self, text: str) -> None:
        """Refocus the original window and paste the draft at the caret."""
        if self.target_hwnd:
            ctypes.windll.user32.SetForegroundWindow(self.target_hwnd)
            time.sleep(0.25)
        old = pyperclip.paste()
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        pyperclip.copy(old)


class DoubleTapListener:
    """Detects two quick presses of a single key (default Right Ctrl) with nothing else in between."""

    def __init__(self, on_trigger, key: str = "right ctrl", window_ms: int = 350):
        self.on_trigger, self.key, self.window = on_trigger, key, window_ms / 1000
        self.last = 0.0

    def _handler(self, ev):
        if ev.event_type != "down":
            return
        # On Windows the keyboard lib names side-specific modifiers ("right ctrl", "right alt").
        if (ev.name or "").lower() != self.key:
            self.last = 0.0
            return
        now = time.monotonic()
        if now - self.last < self.window:
            self.last = 0.0
            self.on_trigger()
        else:
            self.last = now

    def start(self):
        keyboard.hook(self._handler)
