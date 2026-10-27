# Sentient for Windows (personal, open source)

A Windows rebuild of the features in [Sentient OS](https://sentient-os.ai/) for my own use.

| Sentient OS (Mac) | Here | How |
|---|---|---|
| Click the notch → **Sidekick** takes over with its own cursor | `Ctrl+Alt+Space` (or tray icon) | Claude computer toolset + `pyautogui`, DPI-aware screenshots via `mss` |
| Double-tap right ⌘ → **Double Tap** reply in your voice | Double-tap **Right Ctrl** | UI Automation reads the focused window, plus a screenshot, `writingstyle.md` and your knowledge → draft → paste at the caret |
| **Proactive intelligence** cards each morning | Cards window on login / tray → *Morning cards* | Nightly model pass over the knowledge base; nothing runs until you click |
| Nightly 3 AM run that wakes the Mac | Task Scheduler: `WakeToRun`, AC power only | `python -m sentient setup` |
| Reads Notes, Mail, Calendar, Files, Screenshots | Sticky Notes, Outlook mail + calendar, folders (txt/md/pdf/docx), Screenshots (Windows OCR), OneNote exports | `sentient/sources.py` |
| Knowledge as editable files you own | `%APPDATA%\SentientWin\Knowledge\*.md` | Tray → *Open knowledge folder* |
| Your choice of AI / fully offline | `anthropic`, `ollama`, `openai_compat` (LM Studio, llama.cpp) | `config.json` |
| Kill switch | `Ctrl+Alt+Esc`, or move the mouse into a screen corner | A purple border shows while Sidekick is in control |

## Install

```powershell
cd sentient-win
python -m venv .venv
.venv\Scripts\activate
pip install -e .
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # or: ant auth login
python -m sentient sources              # dry run: what it can read (no AI calls)
python -m sentient build                # first knowledge build
python -m sentient setup                # 3 AM nightly task + start at login
python -m sentient run                  # tray app
```

You need Python 3.10+ on Windows 10 or 11. Outlook sources need classic desktop Outlook; "new Outlook" has no COM API. If you use it, connect Gmail or Microsoft 365 in classic Outlook, or add a Graph connector.

## Commands

`run` · `setup` · `uninstall` · `nightly` · `build [--full]` · `suggest` · `sidekick "task"` · `sources`

## Configuration

`%APPDATA%\SentientWin\config.json` is created on first run. In it you can:
- Turn each source on or off, and set the folders it reads (`sources`).
- Pick providers: set `provider` for the heavy work and `doubletap_provider` for replies. For example, run Double Tap on `ollama` to keep it local and fast.
- Change hotkeys, the nightly time (`nightly_time`) and `sidekick_max_steps`.

**Sidekick needs Claude**, because it uses the computer-use toolset. Everything else also works with local models, so the knowledge build can run 100% offline on Ollama.

## Privacy

- The source readers run locally. Text goes to a model only during the nightly digest, Double Tap and Sidekick. With `ollama` set, that model is on your PC too.
- Nothing records your screen in the background. Screenshots are taken only while Double Tap or Sidekick is running.
- The prompts tell the model to leave secrets out of the knowledge base. Still, check `Knowledge\` now and then. You can edit or delete anything in it.

## Safety notes on Sidekick

Sidekick drives your **real** desktop. It must call `ask_user` for approval before it pays, sends, submits, deletes or accepts anything, and it never types passwords or card numbers. Approval prompts are rules in the prompt, not hard guarantees: stay nearby while it works. Windows also blocks it from controlling elevated (admin) windows (UIPI).

## Layout

```
sentient/
  config.py     settings + paths
  llm.py        Anthropic / Ollama / OpenAI-compatible providers
  sources.py    Outlook, Sticky Notes, folders, screenshots (OCR), OneNote exports
  knowledge.py  nightly build -> Knowledge/*.md
  proactive.py  morning cards
  screen.py     capture, scaling, DPI
  sidekick.py   computer-use agent loop
  doubletap.py  double-tap listener, window reader, drafting, paste
  ui.py         Tk windows (command bar, border, draft popup, cards, approvals)
  scheduler.py  Task Scheduler + autostart
  app.py        tray app wiring
```

## Not done yet (ideas)

- WhatsApp and Messages history. WhatsApp Desktop encrypts its local DB, so the practical route is reading the open chat through UI Automation, which Double Tap already does.
- A Gmail/Graph API connector for people on "new Outlook".
- An embedding search index once the knowledge base outgrows the model's context.
