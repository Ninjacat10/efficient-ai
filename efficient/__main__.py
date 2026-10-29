"""CLI: python -m efficient <command>

  run                 start the tray app (hotkeys, Double Tap, cards)
  setup               create config, install the nightly task and autostart
  nightly             knowledge build + proactive cards (what the 3 AM task runs)
  build [--full]      knowledge build only (--full re-reads everything)
  suggest             regenerate proactive cards and print them
  sidekick "task"     run one Sidekick task from the terminal
  learn-claude        learn from new Claude chats (run by a Claude Code SessionEnd hook)
  sources             show what each enabled source would read (dry run, no AI)
  uninstall           remove the nightly task and autostart
"""
from __future__ import annotations

import json
import logging
import sys

from .config import CONFIG_PATH, KNOWLEDGE_DIR, LOG_DIR, ensure_dirs, load_config, load_state


def _logging():
    ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_DIR / "efficient.log", encoding="utf-8"), logging.StreamHandler()],
    )


def main(argv: list[str]) -> int:
    _logging()
    cmd = argv[0] if argv else "run"

    if cmd == "run":
        from .app import App

        App().run()
    elif cmd == "setup":
        from . import scheduler

        cfg = load_config()
        scheduler.install_nightly(cfg["nightly_time"])
        scheduler.set_autostart(True)
        scheduler.create_shortcuts()
        print(f"Config:    {CONFIG_PATH}\nKnowledge: {KNOWLEDGE_DIR}\nNightly task installed for {cfg['nightly_time']}, autostart enabled,"
              " and an 'Efficient AI' shortcut added to the Start Menu and Desktop.")
    elif cmd == "uninstall":
        from . import scheduler

        scheduler.uninstall_nightly()
        scheduler.set_autostart(False)
        print("Removed nightly task and autostart. Your knowledge folder was left in place.")
    elif cmd == "nightly":
        from . import knowledge, proactive

        knowledge.build()
        proactive.generate()
    elif cmd == "build":
        from . import knowledge

        print(json.dumps(knowledge.build(full="--full" in argv), indent=2))
    elif cmd == "learn-claude":
        # Called by a Claude Code SessionEnd hook: hand off to a detached process so Claude isn't kept waiting.
        if "--now" not in argv:
            import subprocess

            subprocess.Popen([sys.executable, "-m", "efficient", "learn-claude", "--now"],
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW, close_fds=True)
            return 0
        from . import knowledge

        logging.getLogger(__name__).info("learned from Claude session: %s", knowledge.build(only=["ai_chats"]))
    elif cmd == "suggest":
        from . import proactive

        for c in proactive.generate():
            print(f"\n[{c.get('tag', '')}] {c['title']}\n  {c.get('body', '')}\n  -> {c['action']}: {c.get('action_label', '')}")
    elif cmd == "sidekick":
        from .sidekick import Sidekick

        task = " ".join(argv[1:]) or input("Task: ")
        sk = Sidekick(ask=lambda q: input(f"\n[Sidekick asks] {q}\n> "), status=print)
        print(sk.run(task))
    elif cmd == "sources":
        from . import sources

        state = load_state()
        items = sources.collect(load_config()["sources"], dict(state))  # copy: dry run doesn't advance cursors
        for i in items[:200]:
            print(f"[{i['source']}] {i['when']}  {i['title'][:90]}")
        print(f"\n{len(items)} items total")
    else:
        print(__doc__)
        return 1
    return 0


def _entry():
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    _entry()
