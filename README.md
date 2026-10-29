<p align="center">
  <img src="assets/logo.png" width="112" alt="Efficient AI logo">
</p>

<h1 align="center">Efficient AI</h1>

<p align="center">An open-source personal AI assistant for Windows.<br>It learns your work overnight, drafts replies in your voice, and finishes tasks on your PC while you watch.</p>

---

## What it does

| Feature | How you use it |
|---|---|
| **Sidekick** – it drives your mouse and keyboard to finish a task | `Ctrl+Shift+Space`, then type or speak the task |
| **Voice** – speak instead of typing, in any box | `Ctrl+M` in the Sidekick box (Windows voice typing, free) |
| **Double Tap** – a reply drafted in your voice | Double-tap **Right Ctrl** in any email or chat |
| **Morning cards** – what needs you today, ready to act on | Shown at login; press `1`–`9` to act |
| **Nightly knowledge** – reads your mail, notes and files at 3 AM | Automatic; it's all plain files you can edit |
| **Home window** | `Ctrl+Alt+E`, the tray icon, or the Start Menu |
| **Claude Code / Cowork** | Buttons on the home window (they use your Claude plan) |

Everything works from the keyboard. Every window lists its shortcuts at the bottom, and `Esc` always closes.

## Install

**Easiest:** download `EfficientAI-Setup.exe` from [Releases](https://github.com/Ninjacat10/efficient-ai/releases), run it, and paste a free Gemini key when it asks. No Python or terminal needed.

**Build the installer yourself:** install [uv](https://docs.astral.sh/uv/) and Inno Setup (`winget install JRSoftware.InnoSetup`), then run `packaging\build.ps1`. It produces `dist\EfficientAI-Setup-<version>.exe`.

**From source** (for development):

```powershell
git clone https://github.com/Ninjacat10/efficient-ai
cd efficient-ai
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e .
setx GEMINI_API_KEY "your-key"          # free key from aistudio.google.com
.venv\Scripts\python -m efficient setup  # Start Menu + Desktop icon, start at login, nightly run
```

Then open **Efficient AI** from the Start Menu.

## Choose your AI

Set these in `%APPDATA%\EfficientAI\config.json` (home window → Settings):

| Setting | Options |
|---|---|
| `provider` (knowledge + cards) | `gemini`, `claude_code` (your Claude plan), `anthropic`, `ollama` (offline), `openai_compat` |
| `doubletap_provider` | same as above |
| `sidekick_provider` | `gemini` or `anthropic` (it needs a model that can see the screen and call tools) |

Free Gemini keys have per-minute and daily limits. Efficient AI waits and retries when it hits them, so long Sidekick tasks may pause, but they won't fail.

## Mail and data sources

- **Outlook** (classic desktop app): mail and calendar, including school or work Microsoft 365 accounts you've added to it.
- **Yahoo, Gmail, iCloud** over IMAP with an app password (`sources.imap_mail` in config).
- Sticky Notes, folders (txt/md/pdf/docx), Screenshots (Windows OCR), OneNote exports.

## Privacy and safety

- The readers run on your PC. Text goes to the model you chose only during the nightly build, Double Tap and Sidekick.
- Nothing records your screen in the background.
- Sidekick asks before it pays, sends, submits or deletes anything, and never types passwords. Stop it any time with `Ctrl+Alt+Esc` or by moving the mouse into a screen corner.

## Layout

```
efficient/
  app.py        tray app, hotkeys, home window wiring
  ui.py         all windows (home, Sidekick box + voice, status, Double Tap, cards)
  brand.py      logo and colors
  sidekick.py   computer-use agent (Claude or Gemini)
  doubletap.py  reads the focused window, drafts, pastes
  sources.py    Outlook, IMAP, Sticky Notes, folders, screenshots
  knowledge.py  nightly build -> Knowledge/*.md
  proactive.py  morning cards
  llm.py        providers
  scheduler.py  nightly task, autostart, shortcuts
```

Inspired by [Sentient OS](https://sentient-os.ai/) for Mac. MIT licensed.
