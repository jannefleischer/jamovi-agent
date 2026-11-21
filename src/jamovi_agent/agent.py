"""Tool-calling agent loop.

For every user message the agent alternates between the LLM and the tools
until the LLM answers without tool calls, or a limit (steps, time) is hit.
Progress is yielded as events, which the web layer streams to the client:

- ``{"type": "tool_call", "name", "arguments"}``
- ``{"type": "tool_result", "name", "result"}`` (shortened)
- ``{"type": "message", "content"}`` final answer
- ``{"type": "error", "message"}``
- ``{"type": "done", "steps", "usage"}``

History is kept per jamovi instance, in memory (lost on restart).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import AsyncIterator

from .llm import LLMClient, LLMConfig, LLMError
from .tools import Toolbox

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a statistics assistant built into jamovi. You work directly in the \
user's open jamovi window through tools: you can inspect the data set and \
create, change and read analyses. Analyses you create appear in the user's \
results panel.

How to work:
1. Look at the data first (get_dataset_schema, describe_variables) before \
suggesting or running an analysis.
2. Find the right analysis with search_analyses and check its options with \
get_analysis_schema before create_analysis. Use exact variable and option names.
3. Prefer one well-chosen analysis over many. Turn on the assumption checks \
and effect sizes the analysis offers when they matter.
4. Report numbers only from tool results, never invent or estimate them. \
Name the analysis (id) you refer to.
5. Explain results in plain language: what was tested, the result, what it \
means, and limits (assumptions, sample size).
6. If the request is unclear or the data do not fit, say so and ask.

Answer in the language of the user (default German). Be concise; use short \
Markdown with tables only where they help."""

RESULT_PREVIEW = 600


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Agent:
    """Runs conversations for the instances of one jamovi session."""

    def __init__(self, toolbox: Toolbox, client: LLMClient | None = None):
        self.toolbox = toolbox
        self.client = client or LLMClient(LLMConfig.from_env())
        self.max_steps = _env_int("JAMOVI_AGENT_MAX_STEPS", 12)
        self.timeout = _env_int("JAMOVI_AGENT_TIMEOUT", 300)
        self._histories: dict[str, list[dict]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def history(self, instance_id: str) -> list[dict]:
        """User/assistant messages of an instance (tool traffic left out)."""
        return [m for m in self._histories.get(instance_id, [])
                if m["role"] in ("user", "assistant") and m.get("content")]

    def reset(self, instance_id: str) -> None:
        self._histories.pop(instance_id, None)

    async def run(self, instance_id: str, user_message: str) -> AsyncIterator[dict]:
        lock = self._locks.setdefault(instance_id, asyncio.Lock())
        if lock.locked():
            yield {"type": "error", "message": "the assistant is still busy with the last request"}
            return
        async with lock:
            async for event in self._run(instance_id, user_message):
                yield event

    async def _run(self, instance_id: str, user_message: str) -> AsyncIterator[dict]:
        history = self._histories.setdefault(instance_id, [])
        history.append({"role": "user", "content": user_message})
        tools = self.toolbox.specs()
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout

        for step in range(1, self.max_steps + 1):
            if loop.time() > deadline:
                yield {"type": "error", "message": "time limit reached"}
                break
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
            try:
                reply = await self.client.complete(messages, tools)
            except (LLMError, asyncio.TimeoutError, OSError) as e:
                log.warning("jamovi_agent: LLM call failed: %s", e)
                yield {"type": "error", "message": f"LLM request failed: {e}"}
                break
            for k, v in (reply.pop("_usage", None) or {}).items():
                if k in usage and isinstance(v, int):
                    usage[k] += v

            calls = reply.get("tool_calls") or []
            entry = {"role": "assistant", "content": reply.get("content")}
            if calls:
                entry["tool_calls"] = calls
            history.append(entry)

            if not calls:
                yield {"type": "message", "content": (reply.get("content") or "").strip()}
                yield {"type": "done", "steps": step, "usage": usage}
                return

            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = None
                yield {"type": "tool_call", "name": name, "arguments": args}
                if not isinstance(args, dict):
                    result = {"error": "arguments must be a JSON object"}
                else:
                    result = await self.toolbox.call(name, args)
                text = json.dumps(result, ensure_ascii=False, default=str)
                yield {"type": "tool_result", "name": name,
                       "result": text if len(text) <= RESULT_PREVIEW else text[:RESULT_PREVIEW] + "…"}
                history.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": text})
        else:
            yield {"type": "error", "message": f"step limit ({self.max_steps}) reached"}

        # keep the history consistent for the next turn: end with an assistant message
        history.append({"role": "assistant", "content": "(stopped before finishing)"})
        yield {"type": "done", "steps": self.max_steps, "usage": usage}
