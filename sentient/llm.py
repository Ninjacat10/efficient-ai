"""Pluggable model providers ("your choice of AI").

Every provider exposes chat(system, text, images) -> str. Images are PIL images.
Sidekick (computer use) needs Claude; everything else works with local models too.
"""
from __future__ import annotations

import base64
import io

import requests

from .config import load_config


def png_b64(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class RefusalError(RuntimeError):
    pass


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

    def __init__(self, url: str, model: str):
        self.url, self.model = url.rstrip("/"), model

    def chat(self, system: str, text: str, images=(), **_) -> str:
        content = [{"type": "text", "text": text}] + [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64(i)}"}} for i in images
        ]
        r = requests.post(
            f"{self.url}/chat/completions",
            json={"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]},
            timeout=600,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


def get_provider(kind: str | None = None):
    cfg = load_config()
    kind = kind or cfg["provider"]
    if kind == "anthropic":
        return AnthropicProvider(cfg["anthropic_model"])
    if kind == "ollama":
        return OllamaProvider(cfg["ollama_url"], cfg["ollama_model"])
    if kind == "openai_compat":
        return OpenAICompatProvider(cfg["openai_compat_url"], cfg["openai_compat_model"])
    raise ValueError(f"Unknown provider {kind!r}")
