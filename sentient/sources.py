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

log = logging.getLogger(__name__)

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".rtf", ".html", ".htm"}
DOC_EXT = {".pdf", ".docx"}
IMG_EXT = {".png", ".jpg", ".jpeg"}
MAX_CHARS = 8000
SOURCE_TIMEOUT = 180
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "AppData"}


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _read_file(p: Path) -> str:
    ext = p.suffix.lower()
    try:
        if ext in TEXT_EXT:
            return p.read_text(encoding="utf-8", errors="ignore")[:MAX_CHARS]
        if ext == ".pdf":
            from pypdf import PdfReader

            return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages[:15])[:MAX_CHARS]
        if ext == ".docx":
            import docx

            return "\n".join(par.text for par in docx.Document(str(p)).paragraphs)[:MAX_CHARS]
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


def onenote_export(cfg: dict, since: float) -> Iterator[dict]:
    # OneNote has no stable local DB; export notebooks to .docx/.pdf/.md and point this at the folder.
    yield from (dict(i, source="note") for i in folders(cfg, since))


REGISTRY = {
    "folders": folders,
    "outlook_mail": outlook_mail,
    "outlook_calendar": outlook_calendar,
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
