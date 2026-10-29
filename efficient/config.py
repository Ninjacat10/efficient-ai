"""Settings and on-disk layout.

Everything lives under %APPDATA%\\EfficientAI unless EFFICIENT_HOME is set:
  config.json       settings (sources, provider, hotkeys)
  state.json        incremental-sync cursors
  Knowledge\\        the human-readable knowledge base (markdown you own)
  suggestions.json  proactive cards prepared by the nightly run
  logs\\
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

HOME = Path(os.environ.get("EFFICIENT_HOME") or Path(os.environ.get("APPDATA", Path.home())) / "EfficientAI")
CONFIG_PATH = HOME / "config.json"
STATE_PATH = HOME / "state.json"
KNOWLEDGE_DIR = HOME / "Knowledge"
SUGGESTIONS_PATH = HOME / "suggestions.json"
LOG_DIR = HOME / "logs"

DEFAULTS: dict = {
    # Provider for heavy work (knowledge build, suggestions, Sidekick).
    # "anthropic" | "ollama" | "openai_compat"
    "provider": "anthropic",
    "anthropic_model": "claude-opus-5-5",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "llama3.1:8b",
    "openai_compat_url": "http://localhost:1234/v1",
    "openai_compat_model": "local-model",
    # Gemini via Google's OpenAI-compatible endpoint. Key: GEMINI_API_KEY env var or gemini_api_key.
    "gemini_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    "gemini_model": "gemini-2.5-flash",
    "gemini_api_key": "",
    # Sidekick backend: "anthropic" (Claude computer use) or "gemini".
    "sidekick_provider": "anthropic",
    # How hard Gemini thinks each Sidekick step: "low" (fastest), "medium", "high".
    "sidekick_thinking": "low",
    # Double Tap can use a different (e.g. local, faster) provider.
    "doubletap_provider": "anthropic",
    "sources": {
        "folders": {"enabled": True, "paths": ["~/Documents", "~/Desktop"]},
        "outlook_mail": {"enabled": True, "days": 14, "max_items": 300},
        "outlook_calendar": {"enabled": True, "days_back": 7, "days_ahead": 21},
        # Yahoo needs an app password (Account security -> Generate app password), stored in the env var.
        "imap_mail": {"enabled": False, "days": 14, "max_items": 300, "accounts": [
            {"name": "yahoo", "host": "imap.mail.yahoo.com", "user": "you@yahoo.com", "password_env": "YAHOO_APP_PASSWORD", "sent": "Sent"},
        ]},
        # Claude / ChatGPT data exports (drop the zip in ~/Downloads or a folder listed here) + Claude Code sessions.
        "ai_chats": {"enabled": True, "export_paths": ["~/Downloads"], "claude_code": True, "max_claude": 400, "max_chatgpt": 150},
        "sticky_notes": {"enabled": True},
        "screenshots": {"enabled": True, "paths": ["~/Pictures/Screenshots"]},
        "onenote_export": {"enabled": False, "paths": []},
    },
    "hotkeys": {
        "sidekick": "ctrl+alt+space",
        "doubletap_key": "right ctrl",
        "doubletap_window_ms": 350,
        "kill_switch": "ctrl+alt+esc",
        "home": "ctrl+alt+e",
        "fill_form": "ctrl+alt+f",
    },
    # Start Windows voice typing as soon as the Sidekick box opens (Ctrl+M toggles it either way).
    "voice_on_open": False,
    "claude_code_folder": "~",
    # Files about you (second brain, resume, cover-letter notes) given to every Sidekick and Double Tap run.
    "profile_files": [],
    # Resume to upload when a job application asks for one.
    "resume_path": "",
    "nightly_time": "03:00",
    "sidekick_max_steps": 60,
}


def _merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def ensure_dirs() -> None:
    for d in (HOME, KNOWLEDGE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        save_config(DEFAULTS)
        return deepcopy(DEFAULTS)
    return _merge(DEFAULTS, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))


def save_config(cfg: dict) -> None:
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def secret(name: str) -> str:
    """An env var, falling back to the user's saved one (setx). Apps launched before `setx` never see it otherwise."""
    if os.environ.get(name):
        return os.environ[name]
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return ""


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    ensure_dirs()
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
