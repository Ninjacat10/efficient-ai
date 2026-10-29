"""Local data sources - the Windows equivalents of Notes / Mail / Calendar / Files / Screenshots.

Each source yields Item dicts: {source, id, title, when, text, meta}. Sources are
incremental: they only return things changed since the cursor stored in state.json.
Every source is opt-in via config["sources"] and fails soft (a missing Outlook or
Sticky Notes install just yields nothing).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Iterator

from .config import secret

log = logging.getLogger(__name__)

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".rtf", ".html", ".htm"}
DOC_EXT = {".pdf", ".docx"}
IMG_EXT = {".png", ".jpg", ".jpeg"}
MAX_CHARS = 8000
SOURCE_TIMEOUT = 180
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "AppData"}


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _read_file(p: Path, limit: int = MAX_CHARS) -> str:
    ext = p.suffix.lower()
    try:
        if ext in TEXT_EXT:
            return p.read_text(encoding="utf-8", errors="ignore")[:limit]
        if ext == ".pdf":
            from pypdf import PdfReader

            return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages[:15])[:limit]
        if ext == ".docx":
            import docx

            return "\n".join(par.text for par in docx.Document(str(p)).paragraphs)[:limit]
    except Exception as e:  # corrupt / locked files are common; skip them
        log.debug("skip %s: %s", p, e)
    return ""


def folders(cfg: dict, since: float) -> Iterator[dict]:
    for root in cfg.get("paths", []):
        root = Path(os.path.expanduser(root))
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix.lower() not in TEXT_EXT | DOC_EXT:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_mtime <= since or st.st_size > 20_000_000:
                    continue
                text = _read_file(p)
                if text.strip():
                    yield {"source": "file", "id": str(p), "title": p.name, "when": _iso(st.st_mtime), "text": text, "meta": {"path": str(p)}}


def _outlook():
    import pythoncom
    import win32com.client  # pywin32

    pythoncom.CoInitialize()  # sources run in worker threads
    return win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")


def outlook_mail(cfg: dict, since: float) -> Iterator[dict]:
    try:
        ns = _outlook()
    except Exception as e:
        log.info("Outlook mail unavailable: %s", e)
        return
    cutoff = max(since, (dt.datetime.now() - dt.timedelta(days=cfg.get("days", 14))).timestamp())
    for folder_id, label in ((6, "inbox"), (5, "sent")):
        try:
            items = ns.GetDefaultFolder(folder_id).Items
            items.Sort("[ReceivedTime]", True)
        except Exception:
            continue
        for n, m in enumerate(items):
            if n >= cfg.get("max_items", 300):
                break
            try:
                when = m.ReceivedTime if label == "inbox" else m.SentOn
                ts = dt.datetime(when.year, when.month, when.day, when.hour, when.minute).timestamp()
                if ts <= cutoff:
                    break
                yield {
                    "source": f"mail_{label}",
                    "id": m.EntryID,
                    "title": m.Subject or "(no subject)",
                    "when": _iso(ts),
                    "text": (m.Body or "")[:MAX_CHARS],
                    "meta": {"from": getattr(m, "SenderName", ""), "to": getattr(m, "To", ""), "unread": bool(getattr(m, "UnRead", False))},
                }
            except Exception:
                continue


def outlook_calendar(cfg: dict, since: float) -> Iterator[dict]:
    # Calendar is always re-read in full for the window - upcoming events matter more than "new" ones.
    try:
        ns = _outlook()
        items = ns.GetDefaultFolder(9).Items
    except Exception as e:
        log.info("Outlook calendar unavailable: %s", e)
        return
    items.IncludeRecurrences = True
    items.Sort("[Start]")
    start = dt.datetime.now() - dt.timedelta(days=cfg.get("days_back", 7))
    end = dt.datetime.now() + dt.timedelta(days=cfg.get("days_ahead", 21))
    restricted = items.Restrict(f"[Start] >= '{start:%m/%d/%Y %H:%M %p}' AND [Start] <= '{end:%m/%d/%Y %H:%M %p}'")
    for ev in restricted:
        try:
            yield {
                "source": "calendar",
                "id": f"{ev.EntryID}:{ev.Start}",
                "title": ev.Subject,
                "when": str(ev.Start),
                "text": f"{ev.Subject}\nStart: {ev.Start}\nEnd: {ev.End}\nLocation: {ev.Location}\n{(ev.Body or '')[:1500]}",
                "meta": {"start": str(ev.Start), "end": str(ev.End), "location": ev.Location},
            }
        except Exception:
            continue


def sticky_notes(cfg: dict, since: float) -> Iterator[dict]:
    db = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages/Microsoft.MicrosoftStickyNotes_8wekyb3d8bbwe/LocalState/plum.sqlite"
    if not db.exists():
        return
    tmp = Path(tempfile.mkdtemp()) / "plum.sqlite"
    shutil.copy2(db, tmp)  # the live DB is locked while Sticky Notes runs
    try:
        con = sqlite3.connect(tmp)
        for note_id, text, updated in con.execute("SELECT Id, Text, UpdatedAt FROM Note"):
            # UpdatedAt is .NET ticks (100ns since 0001-01-01)
            ts = (updated or 0) / 10_000_000 - 62_135_596_800
            if ts <= since or not text:
                continue
            clean = "\n".join(line.split(" ", 1)[-1] if line.startswith("\\id=") else line for line in text.splitlines())
            yield {"source": "note", "id": note_id, "title": clean.strip().split("\n")[0][:80], "when": _iso(ts), "text": clean[:MAX_CHARS], "meta": {}}
        con.close()
    except sqlite3.Error as e:
        log.info("Sticky Notes read failed: %s", e)
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


def _ocr(path: Path) -> str:
    try:
        import asyncio

        import winocr
        from PIL import Image

        return asyncio.run(winocr.recognize_pil(Image.open(path), "en")).text
    except Exception as e:
        log.debug("OCR unavailable for %s: %s", path, e)
        return ""


def screenshots(cfg: dict, since: float) -> Iterator[dict]:
    for root in cfg.get("paths", []):
        root = Path(os.path.expanduser(root))
        if not root.exists():
            continue
        for p in root.iterdir():
            if p.suffix.lower() not in IMG_EXT:
                continue
            mtime = p.stat().st_mtime
            if mtime <= since:
                continue
            text = _ocr(p)  # Windows' built-in OCR engine, fully on-device
            if text.strip():
                yield {"source": "screenshot", "id": str(p), "title": p.name, "when": _iso(mtime), "text": text[:MAX_CHARS], "meta": {"path": str(p)}}


def imap_mail(cfg: dict, since: float) -> Iterator[dict]:
    """Any IMAP mailbox (Yahoo, Gmail, iCloud...). Passwords are app passwords read from env vars."""
    import email
    import imaplib
    from email.header import decode_header, make_header
    from email.utils import parsedate_to_datetime

    def hdr(v) -> str:
        return str(make_header(decode_header(v or "")))

    def body(msg) -> str:
        for part in msg.walk() if msg.is_multipart() else [msg]:
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "ignore")
        return ""

    for acct in cfg.get("accounts", []):
        password = secret(acct.get("password_env", ""))
        if not password:
            log.info("IMAP %s: set the %s environment variable", acct.get("name"), acct.get("password_env"))
            continue
        cutoff = max(since, (dt.datetime.now() - dt.timedelta(days=cfg.get("days", 14))).timestamp())
        try:
            with imaplib.IMAP4_SSL(acct["host"], timeout=60) as m:
                m.login(acct["user"], password)
                for folder, label in ((acct.get("inbox", "INBOX"), "inbox"), (acct.get("sent", "Sent"), "sent")):
                    if m.select(f'"{folder}"', readonly=True)[0] != "OK":
                        continue
                    since_str = dt.datetime.fromtimestamp(cutoff).strftime("%d-%b-%Y")
                    ids = m.search(None, f"SINCE {since_str}")[1][0].split()
                    for num in reversed(ids[-cfg.get("max_items", 300):]):
                        typ, data = m.fetch(num, "(BODY.PEEK[])")
                        if typ != "OK" or not data or not isinstance(data[0], tuple):
                            continue
                        msg = email.message_from_bytes(data[0][1])
                        try:
                            ts = parsedate_to_datetime(msg["Date"]).timestamp()
                        except Exception:
                            ts = cutoff + 1
                        if ts <= cutoff:
                            continue
                        yield {
                            "source": f"mail_{label}",
                            "id": f"{acct['name']}:{msg.get('Message-ID', num.decode())}",
                            "title": hdr(msg["Subject"]) or "(no subject)",
                            "when": _iso(ts),
                            "text": body(msg)[:MAX_CHARS],
                            "meta": {"account": acct["name"], "from": hdr(msg["From"]), "to": hdr(msg["To"])},
                        }
        except Exception as e:
            log.warning("IMAP %s failed: %s", acct.get("name"), e)


def _chat_text(turns: list[tuple[str, str]]) -> str:
    return "\n".join(f"{who}: {text.strip()}" for who, text in turns if text and text.strip())[:MAX_CHARS]


def _parse_export(path: Path) -> Iterator[tuple[str, str, str, float, str]]:
    """conversations.json from a Claude or ChatGPT data export -> (app, id, title, timestamp, text)."""
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    for c in data if isinstance(data, list) else []:
        if "chat_messages" in c:  # Claude
            turns = [("Me" if m.get("sender") == "human" else "Claude",
                      m.get("text") or " ".join(p.get("text", "") for p in m.get("content", []) if isinstance(p, dict)))
                     for m in c["chat_messages"]]
            ts = dt.datetime.fromisoformat(c.get("updated_at", "1970-01-01T00:00:00+00:00").replace("Z", "+00:00")).timestamp()
            yield "claude", c.get("uuid", ""), c.get("name") or "Claude chat", ts, _chat_text(turns)
        elif "mapping" in c:  # ChatGPT
            nodes = sorted((n["message"] for n in c["mapping"].values() if n.get("message")), key=lambda m: m.get("create_time") or 0)
            turns = [("Me" if m["author"]["role"] == "user" else "ChatGPT",
                      " ".join(p for p in (m.get("content", {}).get("parts") or []) if isinstance(p, str)))
                     for m in nodes if m["author"]["role"] in ("user", "assistant")]
            yield "chatgpt", c.get("id", ""), c.get("title") or "ChatGPT chat", c.get("update_time") or 0, _chat_text(turns)


def ai_chats(cfg: dict, since: float) -> Iterator[dict]:
    """Your Claude and ChatGPT conversations - what you ask AI says a lot about what you're working on.

    Reads conversations.json from data exports (zip or unzipped) in cfg["export_paths"], plus Claude Code
    sessions on this PC. Claude comes first and gets the bigger share (cfg["max_claude"] vs cfg["max_chatgpt"]).
    """
    import json
    import zipfile

    found: list[tuple[str, str, str, float, str]] = []
    for root in cfg.get("export_paths", []):
        root = Path(os.path.expanduser(root))
        if not root.exists():
            continue
        for f in [root] if root.is_file() else list(root.rglob("conversations.json")) + list(root.glob("*.zip")):
            try:
                if f.suffix == ".zip":
                    with zipfile.ZipFile(f) as z:
                        names = [n for n in z.namelist() if n.endswith("conversations.json")]
                        if not names:
                            continue
                        tmp = Path(tempfile.mkdtemp()) / "conversations.json"
                        tmp.write_bytes(z.read(names[0]))
                        found += _parse_export(tmp)
                        shutil.rmtree(tmp.parent, ignore_errors=True)
                else:
                    found += _parse_export(f)
            except Exception as e:
                log.info("skip chat export %s: %s", f, e)

    if cfg.get("claude_code", True):  # your own prompts from Claude Code sessions on this PC
        for f in (Path.home() / ".claude" / "projects").rglob("*.jsonl"):
            try:
                mtime = f.stat().st_mtime
                if mtime <= since:
                    continue
                turns = []
                for line in f.open(encoding="utf-8", errors="ignore"):
                    ev = json.loads(line)
                    msg = ev.get("message") or {}
                    if ev.get("type") == "user" and isinstance(msg.get("content"), str):
                        turns.append(("Me", msg["content"]))
                if turns:
                    found.append(("claude", str(f), f"Claude Code: {f.parent.name}", mtime, _chat_text(turns)))
            except Exception as e:
                log.debug("skip %s: %s", f, e)

    limits = {"claude": cfg.get("max_claude", 400), "chatgpt": cfg.get("max_chatgpt", 150)}
    for app in ("claude", "chatgpt"):  # Claude first
        chats = sorted((c for c in found if c[0] == app and c[3] > since and c[4]), key=lambda c: -c[3])[:limits[app]]
        for _, cid, title, ts, text in chats:
            yield {"source": f"{app}_chat", "id": cid, "title": title, "when": _iso(ts), "text": text, "meta": {"app": app}}


def onenote_export(cfg: dict, since: float) -> Iterator[dict]:
    # OneNote has no stable local DB; export notebooks to .docx/.pdf/.md and point this at the folder.
    yield from (dict(i, source="note") for i in folders(cfg, since))


REGISTRY = {
    "folders": folders,
    "outlook_mail": outlook_mail,
    "outlook_calendar": outlook_calendar,
    "imap_mail": imap_mail,
    "ai_chats": ai_chats,
    "sticky_notes": sticky_notes,
    "screenshots": screenshots,
    "onenote_export": onenote_export,
}


def collect(sources_cfg: dict, state: dict) -> list[dict]:
    """Run every enabled source, returning new items and advancing per-source cursors in `state`."""
    items: list[dict] = []
    cursors = state.setdefault("cursors", {})
    now = dt.datetime.now().timestamp()
    for name, fn in REGISTRY.items():
        scfg = sources_cfg.get(name, {})
        if not scfg.get("enabled"):
            continue
        since = cursors.get(name, 0.0)
        box: dict = {}

        def run(fn=fn, scfg=scfg, since=since, box=box):
            try:
                box["items"] = list(fn(scfg, since))
            except Exception as e:
                box["error"] = e

        # A source can hang (e.g. Outlook COM waiting on a profile dialog); never let it block the build.
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(SOURCE_TIMEOUT)
        if t.is_alive() or "error" in box:
            log.warning("source %s skipped: %s", name, box.get("error", f"timed out after {SOURCE_TIMEOUT}s"))
            continue
        got = box["items"]
        log.info("source %s: %d new items", name, len(got))
        items.extend(got)
        cursors[name] = now
    return items
