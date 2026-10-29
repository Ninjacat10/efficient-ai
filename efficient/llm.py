"""Pluggable model providers ("your choice of AI").

Every provider exposes chat(system, text, images) -> str. Images are PIL images.
Sidekick (computer use) needs Claude; everything else works with local models too.
"""
from __future__ import annotations

import base64
import io

import requests

from .config import load_config, secret


def png_b64(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class RefusalError(RuntimeError):
    pass


def _daily_quota_hit(r: requests.Response) -> bool:
    """Free Gemini keys have a per-day cap per model; waiting it out means hours, so don't retry."""
    return r.status_code == 429 and "PerDay" in r.text


_exhausted: dict[str, str] = {}  # model -> date its daily quota ran out (skip it until tomorrow)
_discovered: list[str] = []


def _gemini_models(url: str, headers: dict) -> list[str]:
    """Every text chat Gemini model this key can use, newest first, Flash before Pro (fetched once)."""
    import re

    if not _discovered:
        try:
            r = requests.get(url.rsplit("/chat/completions", 1)[0] + "/models", headers=headers, timeout=20)
            ids = [m["id"].removeprefix("models/") for m in r.json().get("data", [])]
        except Exception:
            ids = []
        ok = [m for m in ids if re.fullmatch(r"gemini-(\d[\d.]*-(flash|pro)(-lite)?(-preview)?|(flash|flash-lite|pro)-latest)", m)]
        ver = lambda m: float((re.match(r"gemini-(\d+(?:\.\d+)?)-", m) or [0, 99])[1])  # "-latest" names first
        ok.sort(key=lambda m: ("pro" in m, -ver(m)))
        _discovered.extend(ok or ["gemini-flash-lite-latest", "gemini-flash-latest"])
    return _discovered


def post_with_retry(url: str, tries: int = 6, **kw) -> requests.Response:
    """POST that waits out rate limits (HTTP 429) and brief server errors, as free API tiers hit them often.

    Each Gemini model has its own daily free quota. When one runs out (or isn't available to this key),
    it moves on to the next: `gemini_fallback_models` first, then every other Gemini model the key can use.
    """
    import datetime

    body = kw.get("json") or {}
    model = str(body.get("model", ""))
    if not model.startswith("gemini"):
        return _post_with_retry(url, tries, **kw)
    today = datetime.date.today().isoformat()
    chain = [model] + load_config().get("gemini_fallback_models", []) + _gemini_models(url, kw.get("headers") or {})
    r = None
    for m in dict.fromkeys(chain):
        if _exhausted.get(m) == today:
            continue
        kw["json"] = {**body, "model": m}
        if m != model and kw["json"].get("reasoning_effort") == "none":
            kw["json"]["reasoning_effort"] = "low"  # not every model accepts "none"
        r = _post_with_retry(url, tries, **kw)
        if _daily_quota_hit(r):
            _exhausted[m] = today
        elif r.status_code in (400, 404) and m != model:
            continue  # this model can't take the request; try the next
        else:
            if m != model:
                import logging
                logging.getLogger(__name__).info("%s is out of quota today; used %s", model, m)
            return r
    return r if r is not None else _post_with_retry(url, tries, **{**kw, "json": body})


def _post_with_retry(url: str, tries: int = 6, **kw) -> requests.Response:
    import time

    for n in range(tries):
        try:
            r = requests.post(url, **kw)
        except (requests.ConnectionError, requests.Timeout):  # e.g. Wi-Fi still reconnecting after sleep
            if n == tries - 1:
                raise
            time.sleep(min(60, 5 * 2 ** n))
            continue
        if r.status_code not in (429, 500, 503) or n == tries - 1 or _daily_quota_hit(r):
            return r
        wait = float(r.headers.get("retry-after") or min(60, 5 * 2 ** n))
        time.sleep(wait)
    return r


class AnthropicProvider:
    def __init__(self, model: str):
        import anthropic

        self.client = anthropic.Anthropic()  # ANTHROPIC_API_KEY or `ant auth login` profile
        self.model = model

    def chat(self, system: str, text: str, images=(), effort: str = "medium", max_tokens: int = 16000) -> str:
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64(i)}} for i in images]
        content.append({"type": "text", "text": text})
        resp = self.client.beta.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": effort},
            messages=[{"role": "user", "content": content}],
            # Server-side refusal fallback: reroutes a declined request instead of failing.
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )
        if resp.stop_reason == "refusal":
            raise RefusalError(getattr(resp.stop_details, "explanation", None) or "Model declined the request")
        return "".join(b.text for b in resp.content if b.type == "text").strip()


class OllamaProvider:
    def __init__(self, url: str, model: str):
        self.url, self.model = url.rstrip("/"), model

    def chat(self, system: str, text: str, images=(), **_) -> str:
        msg = {"role": "user", "content": text}
        if images:
            msg["images"] = [png_b64(i) for i in images]
        r = requests.post(
            f"{self.url}/api/chat",
            json={"model": self.model, "stream": False, "messages": [{"role": "system", "content": system}, msg]},
            timeout=600,
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip()


class OpenAICompatProvider:
    """LM Studio, llama.cpp server, vLLM, Jan... anything speaking /v1/chat/completions."""

    def __init__(self, url: str, model: str, key: str = "", extra: dict | None = None):
        self.url, self.model, self.key, self.extra = url.rstrip("/"), model, key, extra or {}

    def chat(self, system: str, text: str, images=(), **_) -> str:
        content = [{"type": "text", "text": text}] + [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64(i)}"}} for i in images
        ]
        r = post_with_retry(
            f"{self.url}/chat/completions",
            headers={"Authorization": f"Bearer {self.key}"} if self.key else {},
            json={"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}], **self.extra},
            timeout=600,
        )
        if _daily_quota_hit(r):
            raise RuntimeError("Your free Gemini key has used up today's requests on every model. It resets at midnight Pacific time; "
                               "or switch provider to claude_code or ollama in Settings.")
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


class ClaudeCodeProvider:
    """Runs the Claude Code CLI headless (`claude -p`), so it uses your Claude Pro/Max login, not API credit.

    Images are saved to temp files and Claude Code is allowed to Read them.
    """

    def chat(self, system: str, text: str, images=(), **_) -> str:
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path

        exe = shutil.which("claude")
        if not exe:
            raise RuntimeError("Claude Code isn't installed. Install it from claude.com/claude-code and run `claude` once to log in.")
        tmp = Path(tempfile.mkdtemp())
        paths = []
        for n, img in enumerate(images):
            p = tmp / f"screen{n}.png"
            img.save(p)
            paths.append(str(p))
        prompt = text + "".join(f"\n\nScreenshot: {p}" for p in paths)
        args = [exe, "-p", "--output-format", "text", "--append-system-prompt", system]
        if paths:
            args += ["--allowedTools", "Read"]
        try:
            r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=600,
                               cwd=tmp, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if r.returncode != 0:
            raise RuntimeError(f"Claude Code failed: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout.strip()


def get_provider(kind: str | None = None):
    cfg = load_config()
    kind = kind or cfg["provider"]
    if kind == "anthropic":
        return AnthropicProvider(cfg["anthropic_model"])
    if kind == "ollama":
        return OllamaProvider(cfg["ollama_url"], cfg["ollama_model"])
    if kind == "openai_compat":
        return OpenAICompatProvider(cfg["openai_compat_url"], cfg["openai_compat_model"])
    if kind == "claude_code":
        return ClaudeCodeProvider()
    if kind == "gemini_fast":  # small, fast model for quick jobs like filling forms
        return OpenAICompatProvider(cfg["gemini_url"], cfg["gemini_fast_model"], secret("GEMINI_API_KEY") or cfg["gemini_api_key"],
                                    {"reasoning_effort": "none"})  # no thinking: the fastest answers
    if kind == "ollama_fast":  # fully local/open-source option, e.g. gemma
        return OllamaProvider(cfg["ollama_url"], cfg.get("ollama_fast_model", cfg["ollama_model"]))
    if kind == "gemini":
        import os

        return OpenAICompatProvider(cfg["gemini_url"], cfg["gemini_model"], secret("GEMINI_API_KEY") or cfg["gemini_api_key"])
    raise ValueError(f"Unknown provider {kind!r}")
