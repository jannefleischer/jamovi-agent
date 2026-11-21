"""MCP server (Streamable HTTP transport) over the agent's tools.

A minimal implementation of the Model Context Protocol server side, without
the ``mcp`` SDK (jamovi's Python has no pip). Supported:

- ``POST`` with a JSON-RPC request, notification or batch; answers are
  ``application/json`` (the transport allows that instead of SSE)
- methods ``initialize``, ``ping``, ``tools/list``, ``tools/call``
- notifications (e.g. ``notifications/initialized``) -> ``202 Accepted``
- ``GET`` -> ``405`` (no server-initiated stream), ``DELETE`` -> ends the session

Authentication happens in front of jamovi (JupyterHub token via
jupyter-server-proxy). Tools that change the results panel are marked with
``readOnlyHint: false``; all others are read-only.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from . import __version__

log = logging.getLogger(__name__)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "jamovi-agent", "title": "jamovi (ILS)", "version": __version__}
INSTRUCTIONS = (
    "Tools to work in the user's open jamovi window: inspect the data set, find and run "
    "analyses (they appear in the user's results panel) and read results. Call "
    "get_dataset_schema first and get_analysis_schema before create_analysis. "
    "search_docs/read_doc give local documentation on methods and jamovi usage.")

# JSON-RPC error codes
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


def _error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


class MCPServer:
    """Handles ``<jamovi>/agent/mcp`` for one jamovi session."""

    def __init__(self, toolbox):
        self.toolbox = toolbox
        self.sessions: set[str] = set()

    def tool_list(self) -> list[dict]:
        out = []
        for tool in self.toolbox.tools.values():
            out.append({
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.parameters,
                "annotations": {"readOnlyHint": not tool.writes,
                                "destructiveHint": False,
                                "openWorldHint": False},
            })
        return out

    async def handle(self, request):
        """aiohttp handler for GET/POST/DELETE on the MCP endpoint."""
        from aiohttp import web

        body = await request.text() if request.method == "POST" else ""
        status, payload, headers = await self.process(
            request.method, body, request.headers.get("Mcp-Session-Id", ""))
        if payload is None:
            return web.Response(status=status, headers=headers)
        return web.Response(status=status, headers=headers, content_type="application/json",
                            text=json.dumps(payload, ensure_ascii=False, default=str))

    async def process(self, method: str, body: str, session_id: str = ""
                      ) -> tuple[int, Any, dict]:
        """Transport logic without aiohttp: (HTTP status, JSON body or None, headers)."""
        if method == "DELETE":
            self.sessions.discard(session_id)
            return 204, None, {}
        if method != "POST":
            return 405, None, {"Allow": "POST, DELETE"}
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            return 400, _error(None, PARSE_ERROR, "parse error"), {}

        batch = isinstance(payload, list)
        messages = payload if batch else [payload]
        if not messages:
            return 400, _error(None, INVALID_REQUEST, "empty batch"), {}

        headers = {}
        answers = []
        for msg in messages:
            answer, session = await self._dispatch(msg)
            if session:
                headers["Mcp-Session-Id"] = session
            if answer is not None:
                answers.append(answer)
        if not answers:  # only notifications / client responses
            return 202, None, headers
        return 200, (answers if batch else answers[0]), headers

    async def _dispatch(self, msg: Any) -> tuple[dict | None, str | None]:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "invalid request"), None
        method = msg.get("method")
        id_ = msg.get("id")
        if method is None:  # a response from the client; nothing to do
            return None, None
        if "id" not in msg:  # notification
            return None, None
        params = msg.get("params") or {}

        if method == "initialize":
            wanted = params.get("protocolVersion", PROTOCOL_VERSIONS[0])
            version = wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            session = uuid.uuid4().hex
            self.sessions.add(session)
            return _result(id_, {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            }), session
        if method == "ping":
            return _result(id_, {}), None
        if method == "tools/list":
            return _result(id_, {"tools": self.tool_list()}), None
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name not in self.toolbox.tools:
                return _error(id_, INVALID_PARAMS, f"unknown tool: {name}"), None
            if not isinstance(args, dict):
                return _error(id_, INVALID_PARAMS, "arguments must be an object"), None
            result = await self.toolbox.call(name, args)
            is_error = isinstance(result, dict) and "error" in result
            text = json.dumps(result, ensure_ascii=False, default=str)
            content: dict = {"content": [{"type": "text", "text": text}], "isError": is_error}
            if isinstance(result, dict) and not is_error:
                content["structuredContent"] = result
            return _result(id_, content), None
        return _error(id_, METHOD_NOT_FOUND, f"method not found: {method}"), None
