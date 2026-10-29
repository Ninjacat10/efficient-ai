"""What Efficient AI remembers from using it: your confirmed form answers and a log of tasks.

Saved as plain files in two places:
  - %APPDATA%\\EfficientAI\\Knowledge\\form_answers.json  (what the app reads)
  - <second brain>\\raw\\efficient-ai\\*.md                 (readable notes Claude, ChatGPT or you can use)

Form answers are learned from you, not guessed: when you press Ctrl+Alt+F on a page you've already
reviewed and corrected, every filled field is saved, and next time those answers win over everything else.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from .config import KNOWLEDGE_DIR, load_config

ANSWERS = KNOWLEDGE_DIR / "form_answers.json"
PLACEHOLDER = re.compile(r"^(select|choose|--|please select|select\.\.\.|select one|none)$", re.I)


def _norm(label: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", label.lower()).split())


def second_brain_dir() -> Path | None:
    """Where readable copies go, e.g. ...\\Obsidian Vault\\second-brain\\raw\\efficient-ai (None if not set up)."""
    raw = load_config().get("second_brain_dir", "")
    if not raw:
        return None
    p = Path(raw).expanduser() / "raw" / "efficient-ai"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_answers() -> dict:
    try:
        return json.loads(ANSWERS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def learn(pairs: list[tuple[str, str]], source: str = "") -> int:
    """Save label -> value pairs you've confirmed. Returns how many were new or changed."""
    answers, changed = load_answers(), 0
    for label, value in pairs:
        key = _norm(label)
        value = (value or "").strip()
        if len(key) < 2 or not value or PLACEHOLDER.match(value) or len(value) > 2000:
            continue
        if answers.get(key, {}).get("value") != value:
            answers[key] = {"label": label.strip(), "value": value, "from": source, "updated": dt.date.today().isoformat()}
            changed += 1
    if changed:
        ANSWERS.parent.mkdir(parents=True, exist_ok=True)
        ANSWERS.write_text(json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8")
        _mirror_answers(answers)
    return changed


def recall(label: str) -> str:
    """Your saved answer for a field label, matching reworded labels too ('E-mail address' ~ 'Email')."""
    key = _norm(label)
    if not key:
        return ""
    answers = load_answers()
    if key in answers:
        return answers[key]["value"]
    words = set(key.split())
    best, score = "", 0.0
    for k, v in answers.items():
        kw = set(k.split())
        s = len(words & kw) / len(words | kw)
        if s > score:
            best, score = v["value"], s
    return best if score >= 0.75 else ""


def answers_text() -> str:
    a = load_answers()
    return "\n".join(f"- {v['label']}: {v['value']}" for v in a.values())


def _mirror_answers(answers: dict) -> None:
    d = second_brain_dir()
    if not d:
        return
    lines = ["# Form answers", "", "Answers I've confirmed on job applications and other forms (kept by Efficient AI).", ""]
    lines += [f"- **{v['label']}**: {v['value']}" for v in sorted(answers.values(), key=lambda v: v["label"].lower())]
    (d / "form-answers.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def log_activity(kind: str, what: str, result: str) -> None:
    """Append one line per task to the activity log, in the knowledge folder and the second brain."""
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- {stamp} **{kind}**: {what.strip()[:300]}\n  - Result: {result.strip()[:500]}\n"
    targets = [KNOWLEDGE_DIR / "activity-log.md"]
    if second_brain_dir():
        targets.append(second_brain_dir() / "activity-log.md")
    for p in targets:
        new = not p.exists()
        with p.open("a", encoding="utf-8") as f:
            if new:
                f.write("# Efficient AI activity log\n\nTasks I gave Efficient AI and what happened.\n\n")
            f.write(entry)
