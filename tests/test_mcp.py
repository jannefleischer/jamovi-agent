"""Tests for the MCP transport logic (no aiohttp needed)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jamovi_agent.mcp import MCPServer, PROTOCOL_VERSIONS  # noqa: E402
from jamovi_agent.tools import Tool  # noqa: E402


class FakeToolbox:
    def __init__(self):
        self.tools = {
            "get_dataset_schema": Tool("get_dataset_schema", "schema", {"type": "object"}, None),
            "create_analysis": Tool("create_analysis", "create", {"type": "object"}, None, writes=True),
        }

    async def call(self, name, args):
        if name == "create_analysis" and "ns" not in args:
            return {"error": "bad arguments"}
        return {"tool": name, "args": args}


def post(server, msg, session=""):
    return asyncio.run(server.process("POST", json.dumps(msg), session))


class MCPTests(unittest.TestCase):

    def setUp(self):
        self.s = MCPServer(FakeToolbox())

    def test_initialize(self):
        status, body, headers = post(self.s, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                              "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", body["result"]["capabilities"])
        self.assertIn(headers["Mcp-Session-Id"], self.s.sessions)

    def test_unknown_version_falls_back(self):
        _, body, _ = post(self.s, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(body["result"]["protocolVersion"], PROTOCOL_VERSIONS[0])

    def test_notification_is_accepted(self):
        status, body, _ = post(self.s, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((status, body), (202, None))

    def test_tools_list_annotations(self):
        _, body, _ = post(self.s, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = {t["name"]: t for t in body["result"]["tools"]}
        self.assertTrue(tools["get_dataset_schema"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["create_analysis"]["annotations"]["readOnlyHint"])
        self.assertEqual(tools["create_analysis"]["inputSchema"], {"type": "object"})

    def test_tools_call(self):
        _, body, _ = post(self.s, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "get_dataset_schema", "arguments": {"instance_id": "x"}}})
        res = body["result"]
        self.assertFalse(res["isError"])
        self.assertEqual(json.loads(res["content"][0]["text"])["args"], {"instance_id": "x"})
        self.assertEqual(res["structuredContent"]["tool"], "get_dataset_schema")

    def test_tool_error_is_result_not_protocol_error(self):
        _, body, _ = post(self.s, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                   "params": {"name": "create_analysis", "arguments": {}}})
        self.assertTrue(body["result"]["isError"])

    def test_protocol_errors(self):
        _, body, _ = post(self.s, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                   "params": {"name": "nope"}})
        self.assertEqual(body["error"]["code"], -32602)
        _, body, _ = post(self.s, {"jsonrpc": "2.0", "id": 6, "method": "resources/list"})
        self.assertEqual(body["error"]["code"], -32601)
        status, body, _ = asyncio.run(self.s.process("POST", "{not json"))
        self.assertEqual((status, body["error"]["code"]), (400, -32700))

    def test_batch_and_transport_methods(self):
        status, body, _ = post(self.s, [{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                        {"jsonrpc": "2.0", "method": "notifications/x"}])
        self.assertEqual((status, len(body)), (200, 1))
        self.assertEqual(asyncio.run(self.s.process("GET", ""))[0], 405)
        self.assertEqual(asyncio.run(self.s.process("DELETE", "", "abc"))[0], 204)


if __name__ == "__main__":
    unittest.main()
