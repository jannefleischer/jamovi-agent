"""Tests for the documentation index, prompt loading and Knowledge."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jamovi_agent import prompt  # noqa: E402
from jamovi_agent.docs import DocIndex, parse_front_matter, split_sections, tokenize  # noqa: E402
from jamovi_agent.knowledge import Knowledge  # noqa: E402
from jamovi_agent.tools import Toolbox  # noqa: E402

MANUAL = """---
title: jamovi Handbuch – Filter
source: handbook
license: CC BY-SA 4.0
url: https://example.org/filters
---
# Filter

Filter schließen Fälle von allen Analysen aus.

## Filter anlegen

Über Data > Filters wird ein Filter mit einer Formel wie `age > 18` angelegt.

## Filter deaktivieren

Ein Filter lässt sich mit dem Schalter aktivieren oder deaktivieren.
"""

STATS = """# Mann-Whitney-U-Test

Der Mann-Whitney-U-Test ist die nichtparametrische Alternative zum t-Test,
wenn die Normalverteilung verletzt ist.
"""


def ttest_meta():
    return SimpleNamespace(
        ns="jmv", name="ttestIS", title="Independent Samples T-Test",
        menuGroup="T-Tests", menuSubgroup="", in_menu=True,
        defn={"description": {"main": "Tests whether two groups have the same mean."},
              "options": [
                  {"name": "data", "type": "Data"},
                  {"name": "vars", "type": "Variables", "title": "Dependent Variables",
                   "description": {"ui": "the dependent variables"}},
                  {"name": "mann", "type": "Bool", "title": "Mann-Whitney U",
                   "description": {"R": "perform Mann-Whitney U tests"}},
              ]})


class FakeBridge:
    def catalogue(self):
        yield SimpleNamespace(name="jmv", title="jamovi"), ttest_meta()

    def analysis_meta(self, ns, name):
        return ttest_meta()


def make_docs_dir():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "handbook"))
    os.makedirs(os.path.join(d, "statistik"))
    with open(os.path.join(d, "handbook", "filters.md"), "w", encoding="utf-8") as f:
        f.write(MANUAL)
    with open(os.path.join(d, "statistik", "mann-whitney.md"), "w", encoding="utf-8") as f:
        f.write(STATS)
    with open(os.path.join(d, "system-prompt.md"), "w", encoding="utf-8") as f:
        f.write("ILS prompt for jamovi {{jamovi_version}}.\nModules: {{modules}}\n{{docs_index}}")
    with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
        f.write("# not indexed\n")
    return d


class DocsTests(unittest.TestCase):

    def test_front_matter_and_sections(self):
        meta, body = parse_front_matter(MANUAL)
        self.assertEqual(meta["source"], "handbook")
        self.assertEqual(meta["license"], "CC BY-SA 4.0")
        titles = [t for t, _ in split_sections(body, "x")]
        self.assertEqual(titles, ["Filter", "Filter anlegen", "Filter deaktivieren"])

    def test_tokenize_umlauts(self):
        self.assertIn("schließen", tokenize("Filter schließen Fälle aus"))

    def test_index_and_search(self):
        d = make_docs_dir()
        index = DocIndex()
        self.assertEqual(index.add_markdown_dir(d), 2)  # README and system prompt skipped
        index.add_analyses(FakeBridge().catalogue())
        self.assertEqual(index.sources(), ["analyses", "handbook", "statistik"])

        hits = index.search("Filter deaktivieren")
        self.assertEqual(hits[0]["title"], "Filter deaktivieren")
        hits = index.search("Normalverteilung verletzt")
        self.assertEqual(hits[0]["source"], "statistik")
        hits = index.search("Mann-Whitney", source="analyses")
        self.assertEqual(hits[0]["id"], "analysis:jmv/ttestIS")
        self.assertEqual(index.search("xyzzy"), [])


class PromptTests(unittest.TestCase):

    def test_default_when_no_file(self):
        with mock.patch.dict(os.environ, {"JAMOVI_AGENT_PROMPT_FILE": ""}):
            text, src = prompt.load_prompt("/nonexistent")
        self.assertEqual(src, "builtin")
        self.assertIn("{{docs_index}}", text)

    def test_env_file_wins(self):
        d = make_docs_dir()
        other = os.path.join(d, "other.txt")
        with open(other, "w") as f:
            f.write("from env")
        with mock.patch.dict(os.environ, {"JAMOVI_AGENT_PROMPT_FILE": other}):
            self.assertEqual(prompt.load_prompt(d), ("from env", other))

    def test_render(self):
        out = prompt.render("v{{jamovi_version}} [{{modules}}]\n{{docs_index}}",
                            docs_index="- a", modules=["jmv", "scatr"], version="28.18")
        self.assertEqual(out, "v28.18 [jmv, scatr]\n- a")


class KnowledgeTests(unittest.TestCase):

    def setUp(self):
        self.dir = make_docs_dir()
        self.env = mock.patch.dict(os.environ, {"JAMOVI_AGENT_PROMPT_FILE": ""})
        self.env.start()
        self.k = Knowledge(FakeBridge(), self.dir)

    def tearDown(self):
        self.env.stop()

    def test_prompt_from_docs_dir(self):
        p = self.k.system_prompt
        self.assertTrue(p.startswith("ILS prompt for jamovi"))
        self.assertIn("Modules: jmv", p)
        self.assertIn("handbook: jamovi Handbuch – Filter", p)
        self.assertIn("descriptions of 1 installed analyses", p)
        self.assertEqual(self.k.status()["doc_files"], 2)

    def test_read_section_and_file(self):
        sec = self.k.read("handbook/filters.md#1")
        self.assertEqual(sec["title"], "Filter anlegen")
        self.assertEqual(sec["license"], "CC BY-SA 4.0")
        whole = self.k.read("handbook/filters.md")
        self.assertIn("deaktivieren", whole["text"])
        self.assertIn("error", self.k.read("nope.md"))

    def test_tools(self):
        tb = Toolbox(FakeBridge(), self.k)
        self.assertIn("search_docs", tb.tools)
        res = asyncio.run(tb.call("search_docs", {"query": "Filter anlegen"}))
        self.assertEqual(res["results"][0]["id"], "handbook/filters.md#1")
        res = asyncio.run(tb.call("explain_analysis", {"ns": "jmv", "name": "ttestIS"}))
        self.assertIn("Mann-Whitney U", res["description"])

    def test_toolbox_without_knowledge(self):
        self.assertNotIn("search_docs", Toolbox(FakeBridge()).tools)


if __name__ == "__main__":
    unittest.main()
