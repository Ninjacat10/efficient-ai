"""Form filling that drives the browser directly (Chrome DevTools Protocol), like browser-automation tools do.

Instead of reading the screen and moving the mouse, it reads the page's real form - labels, dropdown options,
checkbox groups, file inputs, including forms inside frames (Greenhouse, Lever and Workday embed theirs) - and
sets values in the page itself. No scrolling, no clicks landing in the wrong place, and uploads skip the file picker.

Needs the browser's remote debugging connection:
  Edge: edge://inspect/#remote-debugging  ->  turn on "Allow remote debugging for this browser instance"
  Chrome: chrome://inspect/#remote-debugging  ->  same switch
fill() returns None when no connection is available, and autofill falls back to the UI Automation filler.
Nothing is ever submitted.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import requests

from . import memory
from .config import load_config
from .knowledge import knowledge_context
from .llm import get_provider

log = logging.getLogger(__name__)

USER_DATA_DIRS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data",
    Path(os.environ.get("LOCALAPPDATA", "")) / "BraveSoftware" / "Brave-Browser" / "User Data",
]


class CDP:
    """A minimal DevTools Protocol client over one browser-level WebSocket (flat sessions)."""

    def __init__(self, ws_url: str):
        import websocket

        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.n = 0

    def send(self, method: str, params: dict | None = None, session: str | None = None, timeout: float = 30) -> dict:
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        end = time.time() + timeout
        while time.time() < end:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.n:
                if "error" in r:
                    raise RuntimeError(f"{method}: {r['error'].get('message')}")
                return r.get("result", {})
        raise TimeoutError(method)

    def alive(self) -> bool:
        try:
            self.send("Browser.getVersion", timeout=3)
            return True
        except Exception:
            return False


_conn: CDP | None = None  # kept open: some browsers ask you to allow each new debugging connection


def _endpoints() -> list[str]:
    """Browser WebSocket URLs to try: a configured port first, then any browser with debugging switched on."""
    urls = []
    port = load_config().get("browser_debug_port")
    if port:
        try:
            urls.append(requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2).json()["webSocketDebuggerUrl"])
        except Exception:
            pass
    for d in USER_DATA_DIRS:
        f = d / "DevToolsActivePort"
        try:
            port, path = f.read_text().split()[:2]
            urls.append(f"ws://127.0.0.1:{port}{path}")
        except Exception:
            continue
    return urls


def connect() -> CDP | None:
    global _conn
    if _conn and _conn.alive():
        return _conn
    for url in _endpoints():
        try:
            _conn = CDP(url)
            if _conn.alive():
                return _conn
        except Exception as e:
            log.info("no debugging connection at %s: %s", url, e)
    _conn = None
    return None


def _front_title() -> str:
    try:
        import win32gui

        return win32gui.GetWindowText(win32gui.GetForegroundWindow())
    except Exception:
        return ""


# ---- Reading the form (runs inside the page) ------------------------------------------------------------

SCAN_JS = r"""
(() => {
  const out = []; const groups = {}; let n = 0;
  const txt = e => (e ? (e.innerText || e.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return (r.width > 0 || r.height > 0) && s.visibility !== 'hidden' && s.display !== 'none'; };
  const clean = t => (t || '').replace(/\s*\*\s*$/, '').trim();
  const labelOf = e => {
    const d = e.ownerDocument; let t = '';
    const lb = e.getAttribute('aria-labelledby');
    if (lb) t = lb.split(/\s+/).map(id => txt(d.getElementById(id))).join(' ');
    if (!t && e.id) { const l = d.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) t = txt(l); }
    if (!t) t = e.getAttribute('aria-label') || '';
    if (!t) { const l = e.closest('label'); if (l) t = txt(l); }
    if (!t) t = e.placeholder || e.name || '';
    return clean(t);
  };
  const questionOf = e => {
    const fs = e.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg && txt(lg)) return clean(txt(lg)); }
    const g = e.closest('[role=group],[role=radiogroup]');
    if (g) { const id = g.getAttribute('aria-labelledby'); if (id) return clean(txt(g.ownerDocument.getElementById(id)));
             if (g.getAttribute('aria-label')) return clean(g.getAttribute('aria-label')); }
    let p = e.parentElement;
    for (let i = 0; i < 7 && p; i++, p = p.parentElement) {
      for (const l of p.querySelectorAll('label,legend,[class*=label],[class*=question],p,h3,h4')) {
        if (!l.contains(e) && !l.querySelector('input') && txt(l).length > 3) return clean(txt(l));
      }
    }
    return '';
  };
  const docs = [document];
  document.querySelectorAll('iframe').forEach(f => { try { if (f.contentDocument) docs.push(f.contentDocument); } catch (_) {} });
  for (const d of docs) for (const e of d.querySelectorAll('input,textarea,select')) {
    const type = (e.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'password'].includes(type)) continue;
    if (type !== 'file' && !vis(e)) continue;
    if (e.disabled || e.readOnly) continue;
    if (type !== 'file') {  // invisible helpers, e.g. the hidden "required" input beside each searchable dropdown
      const r = e.getBoundingClientRect(), s = getComputedStyle(e);
      const combo = e.getAttribute('role') === 'combobox' || e.getAttribute('aria-autocomplete') === 'list';
      if (e.tabIndex < 0 || e.getAttribute('aria-hidden') === 'true') continue;
      if (!combo && (+s.opacity === 0 || r.width < 4 || r.height < 4)) continue;  // a dropdown's typing box is tiny until used
    }
    const id = 'f' + (n++); e.setAttribute('data-eai', id);
    if (type === 'checkbox' || type === 'radio') {
      const q = questionOf(e) || labelOf(e), key = type + '|' + (e.name || q);
      if (!groups[key]) { groups[key] = {kind: type === 'radio' ? 'radios' : 'checks', question: q, ids: [], options: [], value: ''}; out.push(groups[key]); }
      const lab = labelOf(e); groups[key].ids.push(id); groups[key].options.push(lab);
      if (e.checked) groups[key].value += (groups[key].value ? ', ' : '') + lab;
      continue;
    }
    if (type === 'file') {
      out.push({id, kind: 'file', question: questionOf(e) || labelOf(e) || 'File upload', value: e.files && e.files.length ? e.files[0].name : ''});
      continue;
    }
    if (e.tagName === 'SELECT') {
      out.push({id, kind: 'choice', native: true, question: labelOf(e) || questionOf(e),
                options: [...e.options].map(o => o.text.trim()).filter(t => t && !/^(select|choose|--)/i.test(t)),
                value: e.selectedIndex > 0 ? e.options[e.selectedIndex].text.trim() : ''});
      continue;
    }
    if (e.getAttribute('role') === 'combobox' || e.getAttribute('aria-autocomplete') === 'list') {
      out.push({id, kind: 'choice', question: labelOf(e) || questionOf(e), options: [], value: SHOWN(e)});
      continue;
    }
    out.push({id, kind: e.tagName === 'TEXTAREA' ? 'essay' : 'text', question: labelOf(e) || questionOf(e), value: e.value || ''});
  }
  return out;
})()
"""

# What a searchable dropdown currently shows (react-select and similar render the choice beside the input).
SHOWN_JS = r"""
function SHOWN(e) {
  let box = e; for (let i = 0; i < 4 && box.parentElement; i++) box = box.parentElement;
  const v = box.querySelector('[class*=single-value],[class*=singleValue],[class*=selected-value],[class*=multi-value__label]');
  if (v) return (v.innerText || '').trim();
  return e.value || '';
}
"""

HELPERS_JS = SHOWN_JS + r"""
function EL(id) {
  for (const d of [document, ...[...document.querySelectorAll('iframe')].map(f => { try { return f.contentDocument; } catch (_) { return null; } }).filter(Boolean)]) {
    const e = d.querySelector(`[data-eai="${id}"]`); if (e) return e;
  }
  return null;
}
function SETVAL(e, v) {
  const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : e.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  e.focus();
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, v);
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
  e.dispatchEvent(new Event('blur', {bubbles: true}));
}
function OPTIONS() {
  const docs = [document, ...[...document.querySelectorAll('iframe')].map(f => { try { return f.contentDocument; } catch (_) { return null; } }).filter(Boolean)];
  const out = [];
  for (const d of docs) for (const o of d.querySelectorAll('[role=option]')) {
    const r = o.getBoundingClientRect(); if (r.width > 0 && r.height > 0) out.push(o);
  }
  return out;
}
function CLICK(el) {
  for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'])
    el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: el.ownerDocument.defaultView, button: 0}));
}
"""


class Frame:
    """One document that may hold the form: the page, or a cross-site frame inside it."""

    def __init__(self, cdp: CDP, session: str):
        self.cdp, self.session = cdp, session

    def js(self, expr: str, by_value: bool = True):
        r = self.cdp.send("Runtime.evaluate", {"expression": HELPERS_JS + "\n" + expr, "returnByValue": by_value,
                                               "awaitPromise": True}, self.session)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", "page script failed")[:300])
        return r["result"].get("value") if by_value else r["result"]

    def key(self, key: str, code: str, vk: int) -> None:
        for t in ("keyDown", "keyUp"):
            self.cdp.send("Input.dispatchKeyEvent", {"type": t, "key": key, "code": code, "windowsVirtualKeyCode": vk}, self.session)

    def type(self, text: str) -> None:
        self.cdp.send("Input.insertText", {"text": text}, self.session)


def _frames(cdp: CDP) -> list[Frame]:
    """The page in front, plus its embedded cross-site frames."""
    targets = cdp.send("Target.getTargets")["targetInfos"]
    pages = [t for t in targets if t["type"] == "page" and not t["url"].startswith(("edge:", "chrome:", "devtools:", "about:"))]
    if not pages:
        return []
    title = _front_title()
    page = next((p for p in pages if p["title"] and title.startswith(p["title"])), None)
    if page is None:
        attached = [p for p in pages if p.get("attached")] or pages
        page = attached[0]
    sid = cdp.send("Target.attachToTarget", {"targetId": page["targetId"], "flatten": True})["sessionId"]
    frames = [Frame(cdp, sid)]
    try:
        tree = cdp.send("Page.getFrameTree", {}, sid)["frameTree"]
        ids, stack = set(), [tree]
        while stack:
            node = stack.pop()
            ids.add(node["frame"]["id"])
            stack += node.get("childFrames", [])
    except Exception:
        ids = set()
    for t in targets:  # cross-site frames are separate targets whose id is their frame id
        if t["type"] == "iframe" and (t["targetId"] in ids or t.get("parentId") == page["targetId"]):
            try:
                s = cdp.send("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})["sessionId"]
                frames.append(Frame(cdp, s))
            except Exception:
                pass
    return frames


# ---- Filling -------------------------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _best(options: list[str], value: str) -> int:
    """Index of the option matching value: exact, starts-with, contains (ignoring punctuation); -1 if none."""
    want = _norm(value)
    names = [_norm(o) for o in options]
    for test in (lambda n: n == want, lambda n: n.startswith(want), lambda n: want in n, lambda n: n and n in want):
        for i, n in enumerate(names):
            if want and test(n):
                return i
    return -1


def _open_options(fr: Frame, fid: str, query: str = "", want: str = "") -> list[str]:
    """Open a searchable dropdown (typing query to filter) and return the options it shows.

    Sites that search as you type load results from the server, so with a query it waits until the list
    holds a match for `want` (or stops changing)."""
    # clicking these toggles the list, so click only when it's closed
    fr.js(f"(() => {{ const e = EL('{fid}'); e.scrollIntoView({{block: 'center'}}); e.focus(); "
          f"if (e.getAttribute('aria-expanded') !== 'true') CLICK(e.closest('[class*=control]') || e); }})()")
    if query:
        fr.type(query)
    else:
        fr.key("ArrowDown", "ArrowDown", 40)
    opts, steady, end = [], 0, time.time() + (4 if query else 2)
    while time.time() < end:
        time.sleep(0.15)
        now = fr.js("OPTIONS().map(o => (o.innerText || o.textContent || '').trim())") or []
        if want and _best(now, want) >= 0:
            return now
        steady = steady + 1 if now and now == opts else 0
        opts = now
        if opts and (not query and steady >= 1 or steady >= 5):
            return opts
    return opts


def _clear_search(fr: Frame, fid: str) -> None:
    """Empty a searchable dropdown's typing box so the next search starts fresh."""
    fr.js(f"(() => {{ const e = EL('{fid}'); e.focus(); e.select && e.select(); }})()")
    fr.key("Backspace", "Backspace", 8)


STOPWORDS = {"university", "of", "the", "and", "college", "institute", "school", "inc", "ltd", "a", "an", "at", "in", "for"}


def _choose(fr: Frame, f, value: str) -> bool:
    if f.native:
        i = _best(f.options, value)
        if i < 0:
            return False
        return bool(fr.js(f"(() => {{ const e = EL('{f.id}'); const want = {json.dumps(f.options[i])}; "
                          "const o = [...e.options].find(o => o.text.trim() === want); if (!o) return false; "
                          "SETVAL(e, o.value); return true; })()"))
    # searchable: try the full answer, then shorter searches ("Canada (+1)" -> "Canada")
    # some site searches only match single words, so also try the most distinctive words ("Waterloo")
    words = sorted({w for w in re.findall(r"[A-Za-z0-9+]+", value) if w.lower() not in STOPWORDS}, key=len, reverse=True)
    for q in list(dict.fromkeys(x.strip() for x in ("", value, re.split(r"[(,]", value)[0], *words[:2])))[:5]:
        fr.key("Escape", "Escape", 27)
        _clear_search(fr, f.id)
        opts = _open_options(fr, f.id, q, want=value)
        i = _best(opts, value)
        if i < 0:
            continue
        fr.js(f"CLICK(OPTIONS()[{i}])")
        time.sleep(0.25)
        shown = _norm(fr.js(f"SHOWN(EL('{f.id}'))") or "")
        if shown and (shown in _norm(opts[i]) or _norm(opts[i]) in shown):
            return True
    fr.key("Escape", "Escape", 27)  # never Enter: on a form with nothing highlighted it submits
    return False


def _tick(fr: Frame, f, labels) -> bool:
    labels = [labels] if isinstance(labels, str) else list(labels)
    want = {_norm(x) for x in labels}
    on = [_norm(o) in want or any(w and w in _norm(o) for w in want) for o in f.options]
    if f.kind == "radios" and sum(on) != 1:
        return False
    return bool(fr.js(f"""(() => {{
        const ids = {json.dumps(f.ids)}, on = {json.dumps(on)};
        ids.forEach((id, i) => {{ const e = EL(id); if (e && e.checked !== on[i]) e.click(); }});
        return ids.every((id, i) => EL(id).checked === on[i]);
    }})()"""))


def _attach(fr: Frame, f, path: str) -> bool:
    if not path or not Path(path).exists():
        return False
    obj = fr.js(f"EL('{f.id}')", by_value=False)
    fr.cdp.send("DOM.setFileInputFiles", {"files": [path], "objectId": obj["objectId"]}, fr.session)
    time.sleep(1.5)  # the site uploads and shows the file name
    return True


def _set_text(fr: Frame, f, value: str) -> bool:
    return bool(fr.js(f"(() => {{ const e = EL('{f.id}'); e.scrollIntoView({{block: 'center'}}); SETVAL(e, {json.dumps(value)}); "
                      "return e.value.length > 0; })()"))


def fill(status=print) -> str | None:
    """Fill the whole form in the browser tab in front. None if the browser can't be reached this way."""
    from .autofill import PROMPT, _describe, _plausible, ask

    if not re.search(r"Edge|Chrome|Brave", _front_title()):
        return None
    cdp = connect()
    if cdp is None:
        return None
    cfg = load_config()
    t0 = time.time()
    status("Reading the whole form...")
    best, fields = None, []
    for fr in _frames(cdp):
        try:
            got = fr.js(SCAN_JS)
        except Exception as e:
            log.info("frame scan failed: %s", e)
            continue
        if len(got or []) > len(fields):
            best, fields = fr, got
    if not fields:
        return None
    fr = best
    title = _front_title()
    items = [SimpleNamespace(**{"native": False, "options": [], "ids": [], **x}) for x in fields]
    for f in items:
        f.question = f.question or f"({f.kind} field)"
    empty = lambda f: not (f.value or "").strip() or bool(memory.PLACEHOLDER.match(f.value.strip()))

    done, wrong, ask = [], [], []
    filled = [(f.question, f.value) for f in items if f.kind in ("text", "choice", "radios") and not empty(f)]
    learned = memory.learn(filled, source=title) if len(filled) >= max(2, len(items) // 2) else 0

    def put(f, value) -> None:
        if not _plausible(f, value):
            ask.append(f.question)
            return
        status(f"Filling {f.question[:50]}...")
        try:
            if f.kind in ("checks", "radios"):
                ok = _tick(fr, f, value)
            elif f.kind == "file":
                ok = _attach(fr, f, cfg.get("resume_path", ""))
            elif f.kind == "choice":
                ok = _choose(fr, f, str(value))
            else:
                ok = _set_text(fr, f, str(value))
        except Exception:
            log.exception("filling %s failed", f.question)
            ok = False
        (done if ok else wrong).append(f.question[:60])

    rest = []
    for f in items:  # 1) instant: questions you've answered before
        if not empty(f):
            continue
        hit = memory.recall(f.question) if f.kind in ("text", "choice", "radios") else ""
        put(f, [hit] if f.kind == "radios" else hit) if hit else rest.append(f)
    if rest:
        status(f"Reading {sum(f.kind == 'choice' and not f.native for f in rest)} dropdowns...")
        for f in rest:
            if f.kind == "choice" and not f.native:
                status(f"Reading options for {f.question[:40]}...")
                try:
                    f.options = _open_options(fr, f.id)
                finally:
                    fr.key("Escape", "Escape", 27)
        status(f"Asking AI about {len(rest)} field{'s' if len(rest) > 1 else ''}...")
        essays = any(f.kind == "essay" for f in rest)
        llm = get_provider(cfg.get("provider", "gemini") if essays else cfg.get("fast_provider", "gemini_fast"))
        from .autofill import _parse

        plan = _parse(ask(llm, "You fill web forms accurately and honestly. Output JSON only.",
                               PROMPT.format(saved=memory.answers_text() or "(none yet)", title=title,
                                             profile=knowledge_context(30_000), fields=_describe(rest)), effort="low"))
        log.info("form plan: %s", json.dumps(plan)[:3000])
        for k, value in (plan.get("fill") or {}).items():
            if str(k).isdigit() and 0 < int(k) <= len(rest) and value not in (None, "", []):
                put(rest[int(k) - 1], value)
        # essays get their own request when the form-wide one skipped them: they matter most
        essays_left = [f for f in rest if f.kind == "essay" and f.question[:60] not in done]
        if essays_left:
            status(f"Writing {len(essays_left)} answer{'s' if len(essays_left) > 1 else ''}...")
            qs = "\n".join(f"{n}. {f.question}" for n, f in enumerate(essays_left, 1))
            out = _parse(ask(get_provider(cfg.get("provider", "gemini")), 
                "You write job application answers in the applicant's own voice, using only real facts from their "
                "background. Output JSON only.",
                f"<about_user>\n{knowledge_context(30_000)}\n</about_user>\n\n<page>{title}</page>\n\n"
                f"<questions>\n{qs}\n</questions>\n\n"
                'Reply with JSON only: {"<question number>": "<answer>"}. Answer every question. Use concrete projects, '
                "roles, tools and numbers from about_user; never invent experience. When questions ask for separate "
                "examples, use a different project for each. Follow any length or format the question asks for "
                "(default 3-4 sentences: problem, what you built, result).", effort="low"))
            for k, value in out.items():
                if str(k).isdigit() and 0 < int(k) <= len(essays_left) and value:
                    f = essays_left[int(k) - 1]
                    put(f, str(value))
                    plan_ask = plan.get("ask") or []
                    n = str(rest.index(f) + 1)
                    plan["ask"] = [a for a in plan_ask if str(a).strip().rstrip(".") != n]
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
