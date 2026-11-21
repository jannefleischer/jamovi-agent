"""Unit tests that run without jamovi (python3 -m unittest discover -s tests)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jamovi_agent import results  # noqa: E402
from jamovi_agent.agent import Agent  # noqa: E402
from jamovi_agent.tools import Tool, analysis_schema, describe, option_schema  # noqa: E402


# ----- protobuf stand-ins -------------------------------------------------------

class Cell(SimpleNamespace):
    def WhichOneof(self, _):
        for k in ("i", "d", "s", "o"):
            if k in self.__dict__:
                return k
        return None


def cell(**kw):
    kw.setdefault("symbols", [])
    return Cell(**kw)


class Elem(SimpleNamespace):
    def WhichOneof(self, _):
        return self.kind


def elem(kind, title="", **kw):
    base = dict(kind=kind, title=title, name=title.lower(), visible=0,
                error=SimpleNamespace(message=""))
    base.update(kw)
    return Elem(**base)


def column(name, cells, title=None, fmt=""):
    return SimpleNamespace(name=name, title=title or name, superTitle="", format=fmt,
                           cells=cells, visible=0)


class ResultsTests(unittest.TestCase):

    def test_table_markdown(self):
        table = SimpleNamespace(columns=[
            column("var", [cell(s="len")], title=""),
            column("t", [cell(d=-3.0665)]),
            column("p", [cell(d=0.0001)], fmt="zto,pvalue"),
        ], notes=[SimpleNamespace(note="<em>Student's</em> t-test")])
        md = results.table_to_markdown(table, "Independent Samples T-Test")
        self.assertIn("**Independent Samples T-Test**", md)
        self.assertIn("| len | -3.067 | < .001 |", md)
        self.assertIn("_Note: Student's  t-test_", md)

    def test_hidden_columns_and_nested_groups(self):
        hidden = column("hidden", [cell(i=1)])
        hidden.visible = 3
        table = SimpleNamespace(columns=[column("n", [cell(i=60)]), hidden], notes=[])
        root = elem("group", "Descriptives", group=SimpleNamespace(elements=[
            elem("table", "Descriptives", table=table),
            elem("image", "Plot"),
            elem("notice", "", notice=SimpleNamespace(content="Few cases")),
        ]))
        md = results.element_to_markdown(root)
        self.assertIn("| n |", md)
        self.assertNotIn("hidden", md)
        self.assertIn("[plot: Plot]", md)
        self.assertIn("> Few cases", md)

    def test_swapped_table(self):
        table = SimpleNamespace(swapRowsColumns=True, notes=[], columns=[
            column("name", [cell(s="len")], title=""),
            column("n", [cell(i=60)], title="N"),
            column("mean", [cell(d=18.8133)], title="Mean"),
        ])
        md = results.table_to_markdown(table)
        self.assertIn("| name | len |", md)
        self.assertIn("| N | 60 |", md)
        self.assertIn("| Mean | 18.813 |", md)

    def test_folded_table(self):
        table = SimpleNamespace(notes=[], columns=[
            column("stat[n]", [cell(s="N")], title=""),
            column("len[n]", [cell(i=60)], title="len"),
            column("stat[mean]", [cell(s="Mean")], title=""),
            column("len[mean]", [cell(d=18.8133)], title="len"),
        ])
        md = results.table_to_markdown(table)
        self.assertIn("| stat[n] | len |", md)
        self.assertIn("| N | 60 |", md)
        self.assertIn("| Mean | 18.813 |", md)

    def test_syntax_block_skipped(self):
        self.assertEqual(results.element_to_markdown(
            elem("preformatted", "", name="syntax", preformatted="jmv::x()")), "")

    def test_max_rows(self):
        cells = [cell(i=i) for i in range(results.MAX_ROWS + 5)]
        md = results.table_to_markdown(SimpleNamespace(columns=[column("x", cells)], notes=[]))
        self.assertIn("5 more rows not shown", md)


class SchemaTests(unittest.TestCase):

    def test_option_types(self):
        self.assertEqual(option_schema({"name": "vars", "type": "Variables",
                                        "suggested": ["continuous"]})["type"], "array")
        lst = option_schema({"name": "hypothesis", "type": "List",
                             "options": [{"name": "different"}, {"name": "oneGreater"}],
                             "default": "different"})
        self.assertEqual(lst["enum"], ["different", "oneGreater"])
        self.assertEqual(lst["default"], "different")
        self.assertIsNone(option_schema({"name": "data", "type": "Data"}))
        self.assertIsNone(option_schema({"name": "x", "type": "Bool", "hidden": True}))
        self.assertEqual(option_schema({"name": "code", "type": "String", "hidden": True},
                                       settable_hidden=True)["type"], "string")
        num = option_schema({"name": "ciWidth", "type": "Number", "min": 50, "max": 99.9})
        self.assertEqual((num["minimum"], num["maximum"]), (50, 99.9))

    def test_analysis_schema(self):
        meta = SimpleNamespace(defn={"options": [
            {"name": "data", "type": "Data"},
            {"name": "deps", "type": "Variables", "title": "Dependent Variables"},
            {"name": "effectSize", "type": "Bool", "default": False},
        ]})
        props = analysis_schema(meta)["properties"]
        self.assertEqual(sorted(props), ["deps", "effectSize"])


class DescribeTests(unittest.TestCase):

    def test_continuous(self):
        d = describe([1, 2, 3, None], "continuous")
        self.assertEqual((d["n"], d["missing"], d["mean"], d["median"]), (3, 1, 2.0, 2))

    def test_nominal(self):
        d = describe(["a", "b", "a", None], "nominal")
        self.assertEqual(d["levels"], {"a": 2, "b": 1})


# ----- agent loop ---------------------------------------------------------------

class FakeClient:
    """Returns scripted assistant messages and records the requests."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def complete(self, messages, tools=None):
        self.requests.append(messages)
        return self.replies.pop(0)


class FakeToolbox:
    def __init__(self):
        self.calls = []
        self.tools = {"get_dataset_schema": Tool("get_dataset_schema", "", {}, None)}

    def specs(self):
        return [{"type": "function", "function": {"name": "get_dataset_schema"}}]

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"rows": 60}


def run(agen):
    async def collect():
        return [e async for e in agen]
    return asyncio.run(collect())


class AgentTests(unittest.TestCase):

    def test_tool_round_trip(self):
        client = FakeClient([
            {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "get_dataset_schema", "arguments": "{}"}}],
             "_usage": {"prompt_tokens": 10, "completion_tokens": 2}},
            {"content": "60 Fälle."},
        ])
        tb = FakeToolbox()
        agent = Agent(tb, client)
        events = run(agent.run("i1", "Wie viele Fälle?"))
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "tool_result", "message", "done"])
        self.assertEqual(tb.calls, [("get_dataset_schema", {})])
        # second request contains the tool result
        tool_msgs = [m for m in client.requests[1] if m["role"] == "tool"]
        self.assertEqual(json.loads(tool_msgs[0]["content"]), {"rows": 60})
        self.assertEqual(agent.history("i1")[-1]["content"], "60 Fälle.")
        self.assertEqual(events[-1]["usage"]["prompt_tokens"], 10)

    def test_thinking_events(self):
        client = FakeClient([
            {"content": "<think>inline</think>Antwort", "reasoning_content": "field"},
        ])
        agent = Agent(FakeToolbox(), client)
        events = run(agent.run("i1", "x"))
        thinking = [e for e in events if e["type"] == "thinking"]
        self.assertEqual(thinking[0]["content"], "field\n\ninline")
        self.assertEqual([e for e in events if e["type"] == "message"][0]["content"], "Antwort")
        last = agent.history("i1")[-1]
        self.assertEqual(last["content"], "Antwort")
        self.assertEqual(last["thinking"], "field\n\ninline")  # kept for display/saving
        # ... but never sent back to the model
        client.replies.append({"content": "zwei"})
        run(agent.run("i1", "y"))
        self.assertTrue(all("thinking" not in m for m in client.requests[-1]))

    def test_step_limit(self):
        call = {"content": None, "tool_calls": [{"id": "c", "type": "function", "function": {
            "name": "get_dataset_schema", "arguments": "{}"}}]}
        agent = Agent(FakeToolbox(), FakeClient([call] * 3))
        agent.max_steps = 2
        events = run(agent.run("i1", "loop"))
        self.assertIn("step limit", [e for e in events if e["type"] == "error"][0]["message"])
        self.assertEqual(agent._histories["i1"][-1]["role"], "assistant")

    def test_bad_arguments(self):
        agent = Agent(FakeToolbox(), FakeClient([
            {"content": None, "tool_calls": [{"id": "c", "type": "function", "function": {
                "name": "get_dataset_schema", "arguments": "not json"}}]},
            {"content": "ok"},
        ]))
        events = run(agent.run("i1", "x"))
        result = [e for e in events if e["type"] == "tool_result"][0]
        self.assertIn("JSON object", result["result"])


if __name__ == "__main__":
    unittest.main()
