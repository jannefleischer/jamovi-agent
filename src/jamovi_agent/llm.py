"""Minimal async client for an OpenAI-compatible ``/chat/completions`` API.

Uses aiohttp (shipped with jamovi) instead of the openai SDK, so the jamovi
image needs no extra packages. TLS uses the system CA store (the image adds
the internal CA via update-ca-certificates).
"""
from __future__ import annotations

import os
import ssl
from dataclasses import dataclass, field


class LLMError(Exception):
    """The endpoint returned an error or an unusable answer."""


@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    timeout: float = 120.0
    extra_headers: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "LLMConfig":
        headers = {}
        user = os.environ.get("ILS_LLM_USER", "").strip()
        if user:
            # LiteLLM usage accounting per user (same header as Open WebUI)
            headers["X-OpenWebUI-User-Email"] = user
        return cls(
            base_url=os.environ.get("OPENAI_BASE_URL", "").rstrip("/"),
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=os.environ.get("JAMOVI_AGENT_MODEL", "gpt-4o-mini"),
            temperature=float(os.environ.get("JAMOVI_AGENT_TEMPERATURE", "0.2")),
            timeout=float(os.environ.get("JAMOVI_AGENT_LLM_TIMEOUT", "120")),
            extra_headers=headers,
        )


class LLMClient:
    """Sends chat completion requests; one instance can be reused."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._ssl = ssl.create_default_context()

    async def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """One completion; returns the assistant message dict (may hold tool_calls)."""
        cfg = self.config
        if not cfg.base_url:
            raise LLMError("OPENAI_BASE_URL is not set")
        payload: dict = {"model": cfg.model, "messages": messages, "temperature": cfg.temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json", **cfg.extra_headers}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"

        import aiohttp  # jamovi ships it; imported lazily so unit tests need no aiohttp

        timeout = aiohttp.ClientTimeout(total=cfg.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(f"{cfg.base_url}/chat/completions", json=payload,
                                 headers=headers, ssl=self._ssl) as r:
                if r.status != 200:
                    text = await r.text()
                    raise LLMError(f"HTTP {r.status}: {text[:500]}")
                data = await r.json()
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected response: {str(data)[:500]}") from e
        message.setdefault("content", None)
        if "usage" in data:
            message["_usage"] = data["usage"]
        return message
