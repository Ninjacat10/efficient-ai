"""The nightly knowledge build.

Reads new items from every enabled source, has the model fold them into a set of
plain markdown files in Knowledge\\ (you can read, edit or delete any of them):

  profile.md        who you are, work, interests, routines
  people.md         people you interact with and what's open with each
  projects.md       ongoing projects, deadlines, commitments
  open_loops.md     unanswered threads, promises, todos, renewals
  writingstyle.md   how you write (from your sent mail) - powers Double Tap
  daily\\YYYY-MM-DD.md  digest of what came in that day
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from . import sources
from .config import KNOWLEDGE_DIR, ensure_dirs, load_config, load_state, save_state
from .llm import get_provider

log = logging.getLogger(__name__)

TOPICS = ["profile", "people", "projects", "open_loops", "writingstyle"]
BATCH_CHARS = 60_000

SYSTEM = """You maintain a private personal knowledge base for the user, stored as markdown files on their own PC.
Write concise, factual, well-organised markdown. Keep what is still true, update what changed, drop what is stale.
Never invent facts. Prefer concrete details (names, dates, amounts, deadlines) over generalities.
Leave out passwords, card numbers, government IDs and authentication codes even if they appear in the input."""

DIGEST_PROMPT = """Here are new items from the user's local sources (files, mail, calendar, notes, screenshots):

{items}

Write a compact digest of what matters in these items, grouped by theme. For every actionable thing
(an unanswered question, a request, a deadline, a renewal, a booking still to make) include who/what/when.
Output markdown only."""

UPDATE_PROMPT = """Current contents of `{topic}.md` (may be empty):
<current>
{current}
</current>

Today's digest of new information:
<digest>
{digest}
</digest>

{guidance}

Return the full, updated contents of `{topic}.md` as markdown only, no preamble."""

GUIDANCE = {
    "profile": "Maintain a profile of the user: role, work, interests, tools, routines, preferences, taste.",
    "people": "Maintain a list of people: one `## Name` section each with relationship, context and anything open between them and the user.",
    "projects": "Maintain the user's ongoing projects and commitments with status and deadlines. Move finished ones to a `## Done` section.",
    "open_loops": "Maintain a checklist of open loops: unanswered messages, promises the user made, upcoming deadlines, renewals, bookings to make. Remove items that the digest shows are resolved. Each line: `- [ ] what - who - due/when - source`.",
    "writingstyle": "Describe how the user writes (tone, length, greetings, sign-offs, punctuation, emoji, formality per audience) and keep 5-10 short verbatim example replies they wrote. Only use text the user themselves wrote (sent mail).",
}


def _fmt(items: list[dict]) -> list[str]:
    """Render items into text batches that fit a single request."""
    batches, cur, size = [], [], 0
    for it in sorted(items, key=lambda i: i["when"]):
        meta = " ".join(f"{k}={v}" for k, v in it["meta"].items() if v and k != "path")
        block = f"### [{it['source']}] {it['title']} ({it['when']}) {meta}\n{it['text'].strip()}\n"
        if size + len(block) > BATCH_CHARS and cur:
            batches.append("\n".join(cur))
            cur, size = [], 0
        cur.append(block)
        size += len(block)
    if cur:
        batches.append("\n".join(cur))
    return batches


def read_topic(topic: str) -> str:
    p = KNOWLEDGE_DIR / f"{topic}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def profile_context(max_chars: int = 50_000) -> list[str]:
    """The files listed in config "profile_files", read in full (txt, md, pdf, docx)."""
    from pathlib import Path

    out, used = [], 0
    for raw in load_config().get("profile_files", []):
        p = Path(raw).expanduser()
        text = sources._read_file(p, limit=max_chars) if p.is_file() else ""
        if text.strip():
            out.append(f"# profile: {p.name}\n{text[:max_chars - used]}")
            used += len(text)
        if used >= max_chars:
            break
    return out


def knowledge_context(max_chars: int = 40_000) -> str:
    """Everything Efficient knows, concatenated - fed to Sidekick, Double Tap and suggestions.

    Profile files (your "second brain", resume...) go first, read fresh each time, so they're never cut off.
    """
    parts = profile_context()
    parts += [f"# {t}.md\n{read_topic(t)}" for t in TOPICS if read_topic(t)]
    daily = sorted((KNOWLEDGE_DIR / "daily").glob("*.md"))[-3:] if (KNOWLEDGE_DIR / "daily").exists() else []
    parts += [f"# daily/{p.name}\n{p.read_text(encoding='utf-8')}" for p in daily]
    return "\n\n".join(parts)[:max_chars]


def build(full: bool = False, only: list[str] | None = None) -> dict:
    """Run one knowledge build. `only` limits it to some sources (e.g. ["ai_chats"] after a Claude session)."""
    ensure_dirs()
    cfg, state = load_config(), load_state()
    if full:
        state["cursors"] = {}
    srcs = {k: v for k, v in cfg["sources"].items() if not only or k in only}
    items = sources.collect(srcs, state)
    if not items:
        save_state(state)
        log.info("nothing new")
        return {"items": 0}

    llm = get_provider()
    digests = [llm.chat(SYSTEM, DIGEST_PROMPT.format(items=b)) for b in _fmt(items)]
    digest = "\n\n".join(digests)

    day = dt.date.today().isoformat()
    (KNOWLEDGE_DIR / "daily").mkdir(exist_ok=True)
    daily = KNOWLEDGE_DIR / "daily" / f"{day}.md"
    prev = daily.read_text(encoding="utf-8") if daily.exists() else f"# {day}\n"
    daily.write_text(f"{prev.rstrip()}\n\n{digest}\n", encoding="utf-8")  # several builds a day add up

    sent = [i for i in items if i["source"] == "mail_sent"]
    for topic in TOPICS:
        topic_digest = digest
        if topic == "writingstyle":
            if not sent:
                continue
            topic_digest = "\n\n".join(f"Subject: {i['title']}\n{i['text'][:1500]}" for i in sent[:40])
        updated = llm.chat(SYSTEM, UPDATE_PROMPT.format(topic=topic, current=read_topic(topic), digest=topic_digest, guidance=GUIDANCE[topic]))
        (KNOWLEDGE_DIR / f"{topic}.md").write_text(updated + "\n", encoding="utf-8")

    state["last_build"] = dt.datetime.now().isoformat(timespec="seconds")
    save_state(state)
    summary = {"items": len(items), "by_source": {}}
    for i in items:
        summary["by_source"][i["source"]] = summary["by_source"].get(i["source"], 0) + 1
    log.info("knowledge build done: %s", json.dumps(summary))
    return summary
