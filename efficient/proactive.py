"""Proactive intelligence - cards prepared overnight, waiting for your approval in the morning.

Each card: {id, tag, title, body, action: "reply"|"sidekick"|"none", action_label,
draft (for replies), task (for Sidekick), status: "new"|"done"|"dismissed"}.
Nothing runs without you clicking the card's button.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid

from .config import SUGGESTIONS_PATH
from .knowledge import knowledge_context, read_topic
from .llm import get_provider

SYSTEM = """You are a thoughtful personal chief of staff. From the user's private knowledge base, find the few
things most worth their attention today and prepare each one so they only need to approve it.
Good cards: an unanswered message (draft the reply in their style), a subscription about to renew they likely
don't need, a booking or form still to do before a deadline, receipts to file, a group plan stuck in chat.
Only use facts present in the knowledge base. Never invent prices or availability - if research is needed,
make it a Sidekick task that does the research and asks before buying anything."""

PROMPT = """Today is {today}.

<knowledge>
{knowledge}
</knowledge>

Return between 0 and 6 cards as a JSON array, most important first, and nothing else. Each element:
{{"tag": "OVERDUE · 3 DAYS · MAIL" (short uppercase context line),
  "title": "You still haven't replied to Carl.",
  "body": "2-3 sentences: why it matters and what you prepared.",
  "action": "reply" | "sidekick" | "none",
  "action_label": "Reply & add to calendar",
  "draft": "the full reply text, in the user's voice (action=reply only)",
  "task": "precise instructions for the computer-use agent, ending with 'ask me before sending/paying/submitting' (action=sidekick only)"}}

<writing_style>
{style}
</writing_style>"""


def _parse(raw: str) -> list[dict]:
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        cards = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [c for c in cards if isinstance(c, dict) and c.get("title")]


def generate() -> list[dict]:
    llm = get_provider()
    raw = llm.chat(SYSTEM, PROMPT.format(today=dt.date.today().strftime("%A %d %B %Y"), knowledge=knowledge_context(), style=read_topic("writingstyle")), effort="high")
    cards = []
    for c in _parse(raw):
        c.setdefault("action", "none")
        c.update(id=uuid.uuid4().hex[:8], status="new", created=dt.datetime.now().isoformat(timespec="seconds"))
        cards.append(c)
    save(cards + [c for c in load() if c["status"] == "new" and _age_days(c) < 3])
    return cards


def _age_days(card: dict) -> float:
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(card["created"])).total_seconds() / 86400
    except Exception:
        return 99


def load() -> list[dict]:
    if SUGGESTIONS_PATH.exists():
        return json.loads(SUGGESTIONS_PATH.read_text(encoding="utf-8"))
    return []


def save(cards: list[dict]) -> None:
    SUGGESTIONS_PATH.write_text(json.dumps(cards, indent=2), encoding="utf-8")


def set_status(card_id: str, status: str) -> None:
    cards = load()
    for c in cards:
        if c["id"] == card_id:
            c["status"] = status
    save(cards)
