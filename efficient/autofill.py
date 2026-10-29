"""Fast form filling: one quick model call instead of a screenshot-by-screenshot agent.

1. Windows UI Automation lists every field in the front window with its real label, and every dropdown's options.
2. One call to a small, fast model maps your profile onto those fields (dropdowns get the exact option text,
   so "Canada (+1)" is picked from the list instead of guessed).
3. The values are entered directly. Nothing is ever submitted - you review and press Submit yourself.

Typical page: one model call (~1s) + a few seconds of typing.
"""
from __future__ import annotations

import json
import logging
import re
import time

import pyautogui

from . import memory
from .config import load_config
from .knowledge import knowledge_context
from .llm import get_provider
from .screen import settle
from .sidekick import _field_value, _paste, find_fields

log = logging.getLogger(__name__)

PROMPT = """Fill this form for the user from what you know about them.

<saved_answers>
Answers the user has personally confirmed on earlier forms. These win over everything else - reuse them
whenever a field asks the same thing, even if worded differently.
{saved}
</saved_answers>

<about_user>
{profile}
</about_user>

<fields>
{fields}
</fields>

Reply with JSON only: {{"fill": {{"<field number>": "<value>"}}, "ask": ["<label of each field you can't answer>"]}}
Rules:
- Each value must belong to that field's label: email only in an email field, phone only in a phone field, first name only in a first-name field, etc.
- Dropdowns: the value must be copied exactly from that field's options.
- Phone country code dropdowns: pick the user's country (e.g. Canada +1). Phone number fields get the number without the country code if a separate code field exists.
- Skip fields that already hold the right value, and fields that are optional and unknown.
- Never guess legal or sensitive answers (work authorization, sponsorship, gender, race, disability, veteran status, criminal record): put them in "ask" unless the user's info states them.
- Short-answer questions ("Why do you want to work here?"): write 2-4 sentences in the user's voice from their real experience."""


def _options(c) -> list[str]:
    """A dropdown's choices (opens it briefly)."""
    names: list[str] = []
    try:
        import uiautomation as auto

        pat = c.GetExpandCollapsePattern()
        pat.Expand()
        time.sleep(0.25)
        for ch, _ in auto.WalkControl(c, includeTop=False, maxDepth=4):
            if ch.ControlTypeName == "ListItemControl" and ch.Name:
                names.append(ch.Name)
            if len(names) >= 260:
                break
        pat.Collapse()
    except Exception:
        pass
    return names


def _describe(fields: list) -> str:
    rows = []
    for n, c in enumerate(fields, 1):
        label = c.Name or c.HelpText or c.AutomationId or "(no label)"
        if c.ControlTypeName == "ComboBoxControl":
            opts = _options(c)
            rows.append(f"{n}. {label} [dropdown] current={_field_value(c)!r} options={json.dumps(opts[:260])}")
        else:
            rows.append(f"{n}. {label} [text] current={_field_value(c)!r}")
    return "\n".join(rows)


def _window_title() -> str:
    try:
        import win32gui

        return win32gui.GetWindowText(win32gui.GetForegroundWindow())[:120]
    except Exception:
        return ""


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def _select(c, value: str) -> bool:
    """Pick a dropdown option by its exact text."""
    import uiautomation as auto

    try:
        pat = c.GetExpandCollapsePattern()
        pat.Expand()
        time.sleep(0.25)
        for ch, _ in auto.WalkControl(c, includeTop=False, maxDepth=4):
            if ch.ControlTypeName == "ListItemControl" and ch.Name.strip().lower() == value.strip().lower():
                try:
                    ch.GetSelectionItemPattern().Select()
                except Exception:
                    ch.Click(simulateMove=False)
                try:
                    pat.Collapse()
                except Exception:
                    pass
                return True
        pat.Collapse()
    except Exception:
        pass
    # fallback: focus it and type the option's first letters
    r = c.BoundingRectangle
    pyautogui.click((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    pyautogui.write(value[:25], interval=0.02)
    pyautogui.press("enter")
    return False


def fill(status=print) -> str:
    """Fill the form in the front window. Returns a summary for a toast."""
    import uiautomation as auto

    cfg = load_config()
    t0 = time.time()
    with auto.UIAutomationInitializerInThread():
        status("Reading the form...")
        fields = find_fields()
        if not fields:
            return "No form fields found in the front window. Click into the form page first, or use Sidekick."
        # A page that's already mostly filled means you've reviewed it: learn your answers first.
        current = [(c.Name, _field_value(c)) for c in fields if c.Name]
        filled = [p for p in current if p[1] and not memory.PLACEHOLDER.match(p[1].strip())]
        learned = memory.learn(filled, source=_window_title()) if len(filled) >= max(2, len(fields) // 2) else 0
        empty = [c for c in fields if not _field_value(c) or memory.PLACEHOLDER.match(_field_value(c).strip())]

        done, wrong = [], []

        def put(c, value: str):
            label = c.Name or "field"
            status(f"Filling {label}...")
            try:
                c.GetScrollItemPattern().ScrollIntoView()
            except Exception:
                pass
            if c.ControlTypeName == "ComboBoxControl":
                _select(c, value)
            else:
                r = c.BoundingRectangle
                pyautogui.click((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                settle(0.1)
                pyautogui.hotkey("ctrl", "a")
                _paste(value)
            got = _field_value(c)
            (done if not got or value.lower()[:6] in got.lower() else wrong).append(label)

        # 1) Instant: fields you've answered before - filled right away, no AI call.
        rest = []
        for c in empty:
            hit = memory.recall(c.Name or "")
            put(c, hit) if hit else rest.append(c)

        # 2) One quick AI call for the new fields only.
        plan: dict = {}
        if rest:
            status(f"Asking AI about {len(rest)} new field{'s' if len(rest) > 1 else ''}...")
            llm = get_provider(cfg.get("fast_provider", "gemini_fast"))
            plan = _parse(llm.chat("You fill web forms accurately. Output JSON only.",
                                   PROMPT.format(saved=memory.answers_text() or "(none yet)",
                                                 profile=knowledge_context(20_000), fields=_describe(rest)), effort="low"))
            for key, value in (plan.get("fill") or {}).items():
                if str(key).isdigit() and 0 < int(key) <= len(rest) and value not in (None, ""):
                    put(rest[int(key) - 1], str(value))
    ask = plan.get("ask") or []
    msg = f"Filled {len(done)} fields in {time.time() - t0:.0f}s."
    if learned:
        msg = f"Learned {learned} of your answers. " + msg
    memory.log_activity("Form fill", _window_title(), msg)
    if wrong:
        msg += f" Check: {', '.join(wrong)}."
    if ask:
        msg += f" Needs you: {', '.join(ask[:6])}."
    return msg + " Review it, then submit yourself. More pages? Press Ctrl+Alt+F again."
