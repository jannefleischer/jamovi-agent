"""HTTP API of the agent, mounted into jamovi's main aiohttp app.

Paths are relative to the jamovi root. Access control is done in front of
jamovi (JupyterHub login via jupyter-server-proxy); jamovi itself only
listens on localhost.
"""
from __future__ import annotations

import json
import logging
import os

from aiohttp import web

from .bridge import Bridge, BridgeError
from .knowledge import Knowledge
from .tools import Toolbox

log = logging.getLogger(__name__)


def _json(data, status: int = 200) -> web.Response:
    return web.Response(status=status, content_type="application/json",
                        text=json.dumps(data, ensure_ascii=False, default=str))


def _enabled() -> bool:
    return os.environ.get("JAMOVI_AGENT_ENABLED", "true").lower() not in ("0", "false", "no")


async def _body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        data = await request.json()
    except (ValueError, json.JSONDecodeError):
        raise web.HTTPBadRequest(text="body must be JSON")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="body must be a JSON object")
    return data


class AgentWeb:
    """Route handlers; one object per jamovi session."""

    def __init__(self, session):
        self.bridge = Bridge(session)
        self.knowledge = Knowledge(self.bridge)
        self.toolbox = Toolbox(self.bridge, self.knowledge)
        self._agent = None

    @property
    def agent(self):
        if self._agent is None:
            from .agent import Agent
            self._agent = Agent(self.toolbox, system_prompt=lambda: self.knowledge.system_prompt)
        return self._agent

    def routes(self) -> list[tuple[str, object]]:
        routes = [("/agent/health", self.health)]
        if not _enabled():
            return routes
        return routes + [
            ("/agent/instances", self.instances),
            ("/agent/tools", self.tools),
            ("/agent/tools/{name}", self.call_tool),
            ("/agent/chat", self.chat),
            ("/agent/history", self.history),
            ("/agent/reset", self.reset),
            ("/agent/prompt", self.prompt),
        ]

    async def health(self, request: web.Request) -> web.Response:
        from .llm import LLMConfig
        cfg = LLMConfig.from_env()
        data = {"ok": True, "enabled": _enabled(), "model": cfg.model,
                "endpoint_configured": bool(cfg.base_url)}
        if _enabled():
            try:
                data["knowledge"] = self.knowledge.status()
            except Exception as e:  # health must always answer
                data["knowledge"] = {"error": str(e)}
        return _json(data)

    async def instances(self, request: web.Request) -> web.Response:
        return _json([self.bridge.instance_summary(i) for i in self.bridge.instances()])

    async def tools(self, request: web.Request) -> web.Response:
        return _json(self.toolbox.specs())

    async def call_tool(self, request: web.Request) -> web.Response:
        if request.method != "POST":
            raise web.HTTPMethodNotAllowed(request.method, ["POST"])
        name = request.match_info["name"]
        if name not in self.toolbox.tools:
            return _json({"error": f"unknown tool: {name}"}, 404)
        result = await self.toolbox.call(name, await _body(request))
        status = 400 if isinstance(result, dict) and "error" in result else 200
        return _json(result, status)

    async def chat(self, request: web.Request) -> web.StreamResponse:
        """Run one agent turn; streams events as Server-Sent Events."""
        if request.method != "POST":
            raise web.HTTPMethodNotAllowed(request.method, ["POST"])
        body = await _body(request)
        message = str(body.get("message", "")).strip()
        if not message:
            return _json({"error": "message is empty"}, 400)
        try:
            inst = self.bridge.instance(body.get("instance_id"))
        except BridgeError as e:
            return _json({"error": str(e)}, 400)

        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                           "Cache-Control": "no-cache",
                                           "X-Accel-Buffering": "no"})
        await resp.prepare(request)
        async for event in self.agent.run(inst.id, message):
            data = json.dumps(event, ensure_ascii=False, default=str)
            await resp.write(f"event: {event['type']}\ndata: {data}\n\n".encode())
        await resp.write_eof()
        return resp

    async def prompt(self, request: web.Request) -> web.Response:
        """The rendered system prompt (for checking what the LLM gets)."""
        return web.Response(text=self.knowledge.system_prompt, content_type="text/plain")

    async def history(self, request: web.Request) -> web.Response:
        try:
            inst = self.bridge.instance(request.query.get("instance_id"))
        except BridgeError as e:
            return _json({"error": str(e)}, 400)
        return _json(self.agent.history(inst.id))

    async def reset(self, request: web.Request) -> web.Response:
        if request.method != "POST":
            raise web.HTTPMethodNotAllowed(request.method, ["POST"])
        body = await _body(request)
        try:
            inst = self.bridge.instance(body.get("instance_id"))
        except BridgeError as e:
            return _json({"error": str(e)}, 400)
        self.agent.reset(inst.id)
        return _json({"ok": True})
