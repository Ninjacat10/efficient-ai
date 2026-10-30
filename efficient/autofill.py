"""Fast form filling: one model call for the whole form instead of a screenshot-by-screenshot agent.

1. Windows UI Automation reads the whole page, top to bottom, including parts scrolled out of view:
   text boxes, essay boxes, dropdowns (native and searchable), checkbox/radio questions, and file uploads.
   Each field gets its real question text, so "Yes"/"No" checkboxes know what they're answering.
2. Saved answers you've confirmed before are filled instantly; one model call maps your profile onto the rest
   (dropdowns and checkboxes get exact option text).
3. Values are entered directly, the resume is attached through the file picker, and the page is checked again
   for fields that only appear after an answer (e.g. "Please specify"). Nothing is ever submitted.

Works on Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Google Forms, Microsoft Forms and plain HTML forms.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pyautogui

from . import memory
from .config import load_config
from .knowledge import knowledge_context
from .llm import get_provider
from .screen import settle
from .sidekick import BROWSER_CHROME, _field_value, _paste

log = logging.getLogger(__name__)

STOP = threading.Event()  # set by the Stop button / kill switch; checked before every step


class Stopped(BaseException):
    """BaseException so the per-field `except Exception` handlers can't swallow a stop."""


def check_stop() -> None:
    if STOP.is_set():
        raise Stopped()


def ask_ai(llm, *args, **kw):
    """llm.chat, but a stop doesn't wait for the AI to answer (that can take a minute)."""
    out = {}

    def run():
        try:
            out["v"] = llm.chat(*args, **kw)
        except Exception as e:
            out["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.2)
        check_stop()
    if "e" in out:
        raise out["e"]
    return out["v"]


PROMPT = """Fill this form for the user from what you know about them.

<saved_answers>
Answers the user has personally confirmed on earlier forms. These win over everything else - reuse them
whenever a field asks the same thing, even if worded differently.
{saved}
</saved_answers>

<about_user>
{profile}
</about_user>

<page>{title}</page>

<fields>
{fields}
</fields>

Reply with JSON only: {{"fill": {{"<field number>": <value>}}, "ask": ["<question of each field you can't answer>"]}}
Values:
- [text] / [essay]: a string. [choice]: one option copied exactly from its options (if no options are listed, the text to search for).
- [checkboxes]: a list of option labels to tick, copied exactly. [one of]: a list with exactly one label.
- [file]: "resume" to attach the user's resume. Leave other uploads out and put them in "ask".
Rules:
- Each value must belong to that field's question: email only in an email field, phone only in a phone field, etc.
- Phone country dropdowns: the user's country. Phone numbers without the country code if a separate code field exists.
- Dates: use the format the field shows (e.g. MM/DD/YYYY). Graduation year / end date: only the expected
  graduation stated in the user's info - a current term like "Fall 2026" is NOT a graduation date; else "ask".
- Skip fields that already hold the right value, and optional fields you know nothing about.
- Never guess legal or sensitive answers (work authorization, sponsorship, gender, race, ethnicity, disability,
  veteran status, criminal record, salary): fill them only if saved_answers or about_user states them, else "ask".
- "How did you hear about us": only if the user's info says; otherwise "ask".
- [essay] questions: answer in the user's voice using only real facts from about_user (projects, roles, results,
  numbers). Never invent experience. Match the length asked for; default 3-5 sentences. One example per box when
  the form asks for separate examples.
- [essay] fields are the most important part of an application: always answer them when the user's info has
  relevant work. Never put them in "ask" just because they're long.
- Links: a LinkedIn field takes only a linkedin.com URL, a GitHub field only a github.com URL; "additional link" or
  portfolio fields take the user's best real link (portfolio, GitHub). Never invent URLs - "ask" instead.
- "ask" lists the field numbers you didn't fill."""


@dataclass
class Field:
    kind: str  # text | essay | choice | checks | radios | file
    question: str
    ctrls: list = field(default_factory=list)  # one control, or the checkboxes/radios of one question
    options: list[str] = field(default_factory=list)
    type_ahead: bool = False

    @property
    def ctrl(self):
        return self.ctrls[0]

    def value(self) -> str:
        for _ in range(3):  # the page may be redrawing the control right after a change
            try:
                return self._value()
            except Exception:
                time.sleep(0.3)
        return ""

    def _value(self) -> str:
        if self.kind in ("checks", "radios"):
            return ", ".join(c.Name for c in self.ctrls if _checked(c))
        if self.kind == "choice":
            return _field_value(self.ctrl) or _shown(self.ctrl)
        if self.kind == "file":
            status = (self.ctrls[-1].Name or "") if len(self.ctrls) > 1 else ""
            return "" if not status or "no file" in status.lower() else status
        return _field_value(self.ctrl)

    def key(self):
        try:
            return tuple(self.ctrl.GetRuntimeId())
        except Exception:
            return (self.kind, self.question)


# ---- Reading the page -----------------------------------------------------------------------------------

def _checked(c) -> bool:
    try:
        return c.GetTogglePattern().ToggleState == 1
    except Exception:
        try:
            return c.GetSelectionItemPattern().IsSelected
        except Exception:
            return False


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _shown(c) -> str:
    """The choice a searchable dropdown displays (react-select etc. show it as text beside the input)."""
    import uiautomation as auto

    try:
        box = c.GetParentControl().GetParentControl()
        texts = [t.Name.strip() for t, _ in auto.WalkControl(box, maxDepth=4)
                 if t.ControlTypeName == "TextControl" and t.Name and t.Name.strip().lower() not in ("select...", "select", "*")]
        return " ".join(texts)
    except Exception:
        return ""


def _aria(c, name: str) -> str:
    try:
        m = re.search(rf"(?:^|;){name}=([^;]*)", c.AriaProperties or "")
        return m.group(1).lower() if m else ""
    except Exception:
        return ""


_root = None  # the document holding the form (may be an embedded frame); option lists are searched here
_view = None  # the visible page area, for deciding what's on screen


def _form_root():
    """The document in the front window that holds the form fields (job boards often embed it in a frame)."""
    import uiautomation as auto

    global _root, _view
    win = auto.GetForegroundControl()
    docs = [c for c, _ in auto.WalkControl(win, maxDepth=30) if c.ControlTypeName == "DocumentControl"]
    best, most = win, -1
    for d in docs:
        n = sum(1 for c, _ in auto.WalkControl(d, maxDepth=80) if c.ControlTypeName in ("EditControl", "ComboBoxControl"))
        if n > most:
            best, most = d, n
    _root = best
    _view = docs[0] if docs else win
    for d in docs:
        r = d.BoundingRectangle
        if r.width() > 50 and r.height() > 50:
            _view = d
            break
    return best


def scan() -> list[Field]:
    """Every fillable thing on the page, in reading order, with the question it answers."""
    import uiautomation as auto

    root = _form_root()
    fields: list[Field] = []
    last_text, last_group, group_q = "", "", None
    start = time.time()
    for c, _d in auto.WalkControl(root, includeTop=False, maxDepth=80):
        if time.time() - start > 25 or len(fields) >= 250:
            break
        t, name = c.ControlTypeName, (c.Name or "").strip()
        try:
            r = c.BoundingRectangle
            visible = r.width() > 2 and r.height() > 2
        except Exception:
            visible = False
        if t == "TextControl":
            if len(name) > 12 or (len(name) > 3 and name.endswith(("?", ":"))):  # a question, not "Yes" or a "*"
                last_text = name
            continue
        if t == "GroupControl" and name:
            last_group = name
            continue
        if not visible:
            continue
        if t in ("CheckBoxControl", "RadioButtonControl"):
            kind = "radios" if t == "RadioButtonControl" else "checks"
            # consecutive boxes under the same question form one field
            if group_q == (kind, last_text) and fields and fields[-1].kind == kind and fields[-1].question == (last_text or last_group or fields[-1].question):
                fields[-1].ctrls.append(c)
                fields[-1].options.append(name)
            else:
                group_q = (kind, last_text)
                fields.append(Field(kind, last_text or last_group or name, [c], [name]))
            continue
        if t in ("EditControl", "ComboBoxControl", "ButtonControl"):
            group_q = None
        if t == "EditControl":
            if BROWSER_CHROME.search(name) or _aria(c, "readonly") == "true":
                continue
            essay = _aria(c, "multiline") == "true"
            ta = _aria(c, "autocomplete") in ("list", "both") or (c.AriaRole or "").lower() == "combobox"
            fields.append(Field("essay" if essay else "text", name or last_text, [c], type_ahead=ta))
        elif t == "ComboBoxControl":
            fields.append(Field("choice", name or last_text, [c]))
        elif t == "ButtonControl" and name.lower() in ("attach", "upload", "upload file", "choose file", "browse", "select file"):
            fields.append(Field("file", last_group or last_text or "File upload", [c]))
        elif t == "ButtonControl" and name.lower().startswith(("attach:", "choose file")):
            if fields and fields[-1].kind == "file":
                fields[-1].ctrls.append(c)  # status line, e.g. "Attach: No file chosen"
    return fields


def _popup_options(limit: int = 150) -> list[str]:
    """Option labels currently shown in any open dropdown/listbox on the page."""
    import uiautomation as auto

    names: list[str] = []
    for ch, _ in auto.WalkControl(_root or auto.GetForegroundControl(), includeTop=False, maxDepth=80):
        if ch.ControlTypeName in ("ListItemControl", "MenuItemControl") and ch.Name:
            if (ch.AriaRole or "").lower() in ("option", "menuitem", "") or ch.ControlTypeName == "MenuItemControl":
                names.append(ch.Name.strip())
        if len(names) >= limit:
            break
    return names


def _read_options(f: Field) -> None:
    """Open a dropdown briefly to learn its choices."""
    c = f.ctrl
    try:
        _bring_into_view(c)
        try:
            c.GetExpandCollapsePattern().Expand()
        except Exception:
            _click(c)
        time.sleep(0.35)
        f.options = list(dict.fromkeys(_popup_options()))
    except Exception:
        f.options = []
    finally:
        pyautogui.press("escape")
        time.sleep(0.05)


def _describe(fields: list[Field]) -> str:
    rows = []
    for n, f in enumerate(fields, 1):
        q = f.question if len(f.question) < 500 else f.question[:350] + " ... " + f.question[-140:]
        if f.kind == "choice":
            more = " (long searchable list, only some shown: you may name any real option)" if len(f.options) >= 8 else ""
            rows.append(f"{n}. [choice] {q} | options={json.dumps(f.options[:150])}{more}")
        elif f.kind == "checks":
            rows.append(f"{n}. [checkboxes] {q} | options={json.dumps(f.options)}")
        elif f.kind == "radios":
            rows.append(f"{n}. [one of] {q} | options={json.dumps(f.options)}")
        else:
            rows.append(f"{n}. [{f.kind}] {q}")
    return "\n".join(rows)


# ---- Entering values ------------------------------------------------------------------------------------

def _safe_zone() -> tuple[int, int, int, int]:
    """The part of the page that's safe to click: below sticky headers and banners, above the bottom edge."""
    v = _view.BoundingRectangle
    return v.left + 2, v.top + int(v.height() * 0.22), v.right - 2, v.bottom - 10


def _on_screen(c) -> tuple[int, int] | None:
    """The control's centre if it's visible in the safe part of the page, else None."""
    try:
        r = c.BoundingRectangle
        left, top, right, bottom = _safe_zone() if _view else (0, 0, 10**5, 10**5)
        x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        if r.width() > 2 and r.height() > 2 and left < x < right and top < y < bottom:
            return x, y
    except Exception:
        pass
    return None


def _bring_into_view(c) -> None:
    """Scroll the page until the control sits in the safe zone (focusing it makes the browser scroll to it)."""
    for attempt in (c.SetFocus, lambda: c.GetScrollItemPattern().ScrollIntoView()):
        try:
            attempt()
        except Exception:
            continue
        settle(0.2)
        if _on_screen(c):
            return
        break
    # nudge with the mouse wheel: focus often parks the field under a sticky header at the very top
    for _ in range(20):
        if _on_screen(c) or not _view:
            return
        try:
            r = c.BoundingRectangle
            left, top, right, bottom = _safe_zone()
            pyautogui.moveTo((left + right) // 2, (top + bottom) // 2)
            pyautogui.scroll(300 if (r.top + r.bottom) // 2 <= top else -300)
            settle(0.15)
        except Exception:
            return


def _focused_on(c) -> bool:
    """Is the keyboard cursor in this control? Typing anywhere else would put text in the wrong field."""
    import uiautomation as auto

    try:
        a = auto.GetFocusedControl()
        if a.GetRuntimeId() == c.GetRuntimeId() or (a.Name and a.Name == c.Name):
            return True
        # searchable dropdowns move the cursor to an inner input: accept anything inside the same widget
        box = c.GetParentControl().GetParentControl()
        p = a
        for _ in range(6):
            p = p.GetParentControl()
            if p is None:
                break
            if p.GetRuntimeId() == box.GetRuntimeId():
                return True
        return False
    except Exception:
        return False


def _focus(c) -> None:
    try:
        c.SetFocus()
    except Exception:
        raise RuntimeError("field is no longer on the page")


def _click(c) -> bool:
    _bring_into_view(c)
    pt = _on_screen(c)
    if not pt:
        return False
    pyautogui.click(*pt)
    return True


def _pick(value: str, wait: float = 3) -> str:
    """Click the open list's option matching value (exact, then starts-with, then contains), ignoring punctuation.

    Returns the clicked option's text ("" if none matched)."""
    import uiautomation as auto

    want = _norm(value)
    end = time.time() + wait  # options may load from the site
    while want and time.time() < end:
        time.sleep(0.2)
        exact = starts = contains = None
        for ch, _d in auto.WalkControl(_root or auto.GetForegroundControl(), includeTop=False, maxDepth=80):
            if ch.ControlTypeName not in ("ListItemControl", "MenuItemControl", "TreeItemControl") or not ch.Name:
                continue
            if (ch.AriaRole or "option") not in ("option", "menuitem", "treeitem"):
                continue  # page content lists, not a dropdown
            name = _norm(ch.Name)
            if name == want:
                exact = ch
                break
            if starts is None and name.startswith(want):
                starts = ch
            if contains is None and want in name:
                contains = ch
        hit = exact or starts or contains
        if hit is not None:
            pt = _on_screen(hit)
            if pt:  # click it where it is: focusing an option first would close the menu
                pyautogui.click(*pt)
                return hit.Name
            try:
                hit.GetSelectionItemPattern().Select()  # native lists, or an option scrolled out of the menu
                return hit.Name
            except Exception:
                pass
    return ""


def _is_web(c) -> bool:
    return bool(c.AriaRole)


def _choose(f: Field, value: str) -> None:
    """Dropdowns of every kind: native <select>, searchable (react-select, Workday), and type-ahead boxes."""
    c = f.ctrl
    want = _norm(value)
    # shorter searches for when the site words it differently: "Canada (+1)" -> "Canada"
    queries = list(dict.fromkeys(q.strip() for q in (value, re.split(r"[(,]", value)[0], value.split()[0]) if q.strip()))

    def ok(picked: str) -> bool:
        # the box may show less than the option, e.g. "+1" for "Canada +1"
        got, picked = _norm(f.value()), _norm(picked)
        return bool(got) and (got == want or got.startswith(want) or want.startswith(got) or got in picked)

    _bring_into_view(c)
    try:  # 1) open it and pick from the list
        c.GetExpandCollapsePattern().Expand()
    except Exception:
        _click(c)
    picked = _pick(value, wait=1.5)
    if picked and (time.sleep(0.2) or ok(picked)):
        return
    for q in queries:  # 2) type to search, then pick
        if not _click(c):
            _focus(c)
        settle(0.1)
        if not _focused_on(c):
            raise RuntimeError("cursor isn't in the dropdown")
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("backspace")
        _paste(q[:60])
        picked = _pick(value, wait=2) or _pick(q, wait=0.5)
        if picked and (time.sleep(0.2) or ok(picked)):
            return
    # never press Enter on a web form: with no option highlighted it submits the application
    pyautogui.press("escape")
    raise ValueError(f"no option matching {value!r}")


def _type_into(f: Field, value: str) -> None:
    c = f.ctrl
    _bring_into_view(c)
    if f.type_ahead:
        return _choose(f, value)
    if not _click(c):
        _focus(c)
    settle(0.1)
    if not _focused_on(c):
        raise RuntimeError("cursor isn't in the field")
    pyautogui.hotkey("ctrl", "a")
    _paste(value)


def _tick(f: Field, labels) -> None:
    labels = [labels] if isinstance(labels, str) else list(labels)
    want = {str(x).strip().lower() for x in labels}
    for c in f.ctrls:
        on = (c.Name or "").strip().lower() in want
        if on != _checked(c):
            _bring_into_view(c)
            try:
                c.GetTogglePattern().Toggle()
            except Exception:
                try:
                    c.GetSelectionItemPattern().Select()
                except Exception:
                    _click(c)
            time.sleep(0.1)


def _attach(f: Field, path: str) -> bool:
    """Click Attach and type the file path into the Windows file picker."""
    import uiautomation as auto

    if not path or not Path(path).exists():
        return False
    _bring_into_view(f.ctrl)
    _click(f.ctrl)
    for _ in range(20):  # wait for the Open dialog
        time.sleep(0.25)
        dlg = auto.WindowControl(searchDepth=2, ClassName="#32770")
        if dlg.Exists(0, 0):
            box = dlg.EditControl(AutomationId="1148")  # the "File name" box
            if not box.Exists(0, 0):
                pyautogui.press("escape")
                return False
            box.GetValuePattern().SetValue(path)
            box.SetFocus()
            time.sleep(0.1)
            if not dlg.Exists(0, 0) or auto.GetFocusedControl().ControlTypeName != "EditControl":
                return False
            pyautogui.press("enter")  # inside the file picker only: opens the file
            time.sleep(1.5)  # upload
            return True
    return False


# ---- The whole flow -------------------------------------------------------------------------------------

def pick_file(question: str, title: str, cfg: dict) -> str:
    """The file for an upload field: a resume or cover letter made for this company/role when one exists
    (e.g. Name_Resume_Stripe.pdf for a Stripe page), else the default resume.
    Other uploads (transcripts, portfolios) return "" and are left for you."""
    q = question.lower()
    kind = "cover" if "cover" in q else "resume" if re.search(r"resume|cv|\(file field\)|attach", q) else ""
    if not kind:
        return ""
    dirs = [Path(d).expanduser() for d in cfg.get("application_file_dirs") or
            ["~/Downloads", "~/Documents/Claude outputs", "~/Documents"]]
    if cfg.get("resume_path"):
        dirs.append(Path(cfg["resume_path"]).parent)
    page = set(re.findall(r"[a-z0-9]+", title.lower())) - {"job", "application", "for", "at", "and", "the", "microsoft", "edge", "google", "chrome", "intern", "profile", "personal"}
    best, score = "", (0, 0.0)
    for d in dirs:
        for f in (d.glob("*.pdf") if d.is_dir() else []):
            name = f.stem.lower()
            if (kind == "cover") != ("cover" in name) or (kind == "resume" and "resume" not in name and "cv" not in name):
                continue
            words = set(re.findall(r"[a-z0-9]+", name))
            mark = (len(words & page), f.stat().st_mtime)  # most matching words, then the newest
            if mark[0] and mark > score:
                best, score = str(f), mark
    if best:
        return best
    return cfg.get("resume_path", "") if kind == "resume" else ""


def _window_title() -> str:
    try:
        import win32gui

        return win32gui.GetWindowText(win32gui.GetForegroundWindow())[:120]
    except Exception:
        return ""


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def _stale(f: Field) -> bool:
    """True if the page replaced this control since we read it."""
    try:
        r = f.ctrl.BoundingRectangle
        return r.width() <= 0 or not f.ctrl.Name and f.kind != "file"
    except Exception:
        return True


def _refresh(f: Field) -> None:
    """Find the field again by its question (same kind, same position in the page order)."""
    for g in scan():
        if g.kind == f.kind and g.question == f.question:
            f.ctrls, f.type_ahead = g.ctrls, g.type_ahead
            return


def _plausible(f: Field, value) -> bool:
    """Hard checks the model can't talk its way past."""
    q, v = f.question.lower(), (", ".join(value) if isinstance(value, list) else str(value)).strip()
    if f.kind in ("checks", "radios", "file", "essay"):
        return True
    if "linkedin" in q:
        return "linkedin.com/" in v.lower()
    if "github" in q:
        return "github.com/" in v.lower()
    if "email" in q and "@" not in v:
        return False
    if re.search(r"phone|mobile", q) and f.kind == "text" and len(re.sub(r"\D", "", v)) < 7:
        return False
    if f.kind == "text" and re.search(r"https?://", v) and not re.search(r"link|url|website|portfolio|profile|github|site", q):
        return False  # a URL in a non-link field
    return True


def _empty(f: Field) -> bool:
    v = (f.value() or "").strip()
    return not v or bool(memory.PLACEHOLDER.match(v)) or v.lower() in ("select...", "select", "--")


def fill(status=print) -> str:
    """Fill the whole form in the front window. Returns a summary for a toast.

    In a browser with remote debugging switched on, it fills through the page itself (fast and exact);
    otherwise it reads and types through Windows UI Automation."""
    import uiautomation as auto

    from . import browserfill

    try:
        result = browserfill.fill(status)
        if result:
            return result
    except Exception:
        log.exception("direct browser fill failed; using UI Automation")

    cfg = load_config()
    t0 = time.time()
    done, wrong, ask, learned = [], [], [], 0
    seen: set = set()
    title = _window_title()

    def put(f: Field, value) -> None:
        if not _plausible(f, value):
            log.info("skipped implausible value for %s: %r", f.question, value)
            ask.append(f.question)
            return
        status(f"Filling {f.question[:50]}...")
        for attempt in (1, 2):
            try:
                if attempt == 2 or _stale(f):
                    _refresh(f)  # pages like Greenhouse rebuild fields after every answer
                if f.kind in ("checks", "radios"):
                    _tick(f, value)
                elif f.kind == "file":
                    if not _attach(f, pick_file(f.question, title, cfg)):
                        ask.append(f.question)
                        return
                elif f.kind == "choice":
                    _choose(f, str(value))
                else:
                    _type_into(f, str(value))
                break
            except Exception:
                pyautogui.press("escape")
                if attempt == 2:
                    log.exception("filling %s failed", f.question)
                    wrong.append(f.question[:60])
                    return
        if f.kind in ("file", "choice"):  # file: no readable value; choice: _choose already verified it
            done.append(f.question[:60])
            return
        time.sleep(0.15)
        got = _norm(f.value())
        expect = _norm(", ".join(value) if isinstance(value, list) else str(value))
        digits = lambda x: re.sub(r"\D", "", x)
        same = not got or expect[:6] in got or got[:6] in expect or (digits(expect) and digits(expect) == digits(got)[-len(digits(expect)):])
        (done if same else wrong).append(f.question[:60])

    with auto.UIAutomationInitializerInThread():
        for rnd in range(3):  # answers can reveal new fields ("Please specify", a second page section)
            status("Reading the whole form..." if rnd == 0 else "Checking for new fields...")
            fields = [f for f in scan() if f.key() not in seen]
            if rnd == 0 and not fields:
                time.sleep(0.8)  # browsers build the accessibility tree on the first query
                fields = scan()
            if not fields:
                if rnd == 0:
                    return "No form fields found in the front window. Click into the form page first, or use Sidekick."
                break
            seen.update(f.key() for f in fields)
            if rnd == 0:
                # A page that's already mostly filled means you've reviewed it: learn your answers first.
                filled = [(f.question, f.value()) for f in fields if f.kind in ("text", "choice", "radios") and not _empty(f)]
                if len(filled) >= max(2, len(fields) // 2):
                    learned = memory.learn(filled, source=title)

            todo = [f for f in fields if _empty(f)]
            rest = []
            for f in todo:  # 1) instant: questions you've answered before
                hit = memory.recall(f.question) if f.kind in ("text", "choice", "radios") else ""
                put(f, [hit] if f.kind == "radios" else hit) if hit else rest.append(f)
            if not rest:
                continue
            # 2) one AI call for everything else on the page
            status(f"Reading {sum(f.kind == 'choice' for f in rest)} dropdowns...")
            for f in rest:
                if f.kind == "choice" and not f.options:
                    _read_options(f)
            status(f"Asking AI about {len(rest)} field{'s' if len(rest) > 1 else ''}...")
            essays = any(f.kind == "essay" for f in rest)
            llm = get_provider(cfg.get("provider", "gemini") if essays else cfg.get("fast_provider", "gemini_fast"))
            plan = _parse(ask_ai(llm, "You fill web forms accurately and honestly. Output JSON only.",
                                   PROMPT.format(saved=memory.answers_text() or "(none yet)", title=title,
                                                 profile=knowledge_context(30_000), fields=_describe(rest)),
                                   effort="low"))
            log.info("form plan: %s", json.dumps(plan)[:3000])
            for k, value in (plan.get("fill") or {}).items():
                if str(k).isdigit() and 0 < int(k) <= len(rest) and value not in (None, "", []):
                    put(rest[int(k) - 1], value)
            for a in plan.get("ask") or []:
                a = str(a).strip().rstrip(".")
                ask.append(rest[int(a) - 1].question if a.isdigit() and 0 < int(a) <= len(rest) else a)

    msg = f"Filled {len(done)} fields in {time.time() - t0:.0f}s."
    if learned:
        msg = f"Learned {learned} of your answers. " + msg
    memory.log_activity("Form fill", title, msg)
    if wrong:
        msg += f" Check: {', '.join(wrong[:5])}."
    if ask:
        msg += f" Needs you: {'; '.join(a[:50] for a in dict.fromkeys(ask))[:450]}."
    return msg + " Review it, then submit yourself."
