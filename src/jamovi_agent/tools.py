"""Tools the agent (and the HTTP/MCP API) can call.

Each tool has a JSON schema (OpenAI function-calling format) and an async
implementation on top of ``Bridge``. Results are plain JSON-able dicts; large
outputs are truncated so they fit into an LLM context.
"""
from __future__ import annotations

import itertools
import os
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .bridge import Bridge, BridgeError
from .results import analysis_to_markdown, references

MAX_TEXT = 12000
MAX_DATA_ROWS = 200

_INSTANCE = {
    "instance_id": {
        "type": "string",
        "description": "jamovi window (instance) id; omit to use the open window",
    }
}


def _raw_data_allowed() -> bool:
    return os.environ.get("JAMOVI_AGENT_ALLOW_RAW_DATA", "false").lower() in ("1", "true", "yes")


def _truncate(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…(truncated)"


# ----- option definitions -> JSON schema ------------------------------------

def _option_title(opt: dict) -> str:
    title = opt.get("title") or ""
    desc = opt.get("description")
    if isinstance(desc, dict):
        desc = desc.get("R") or desc.get("ui") or ""
    desc = (desc or "").strip().replace("\n", " ")
    text = title if not desc else f"{title}: {desc}" if title else desc
    return text[:300]


def _list_values(opt: dict) -> list[str]:
    vals = []
    for o in opt.get("options", []) or []:
        vals.append(str(o["name"] if isinstance(o, dict) else o))
    return vals


def option_schema(opt: dict) -> dict | None:
    """JSON schema of one jamovi option definition (None = not settable)."""
    typ = opt.get("type")
    if opt.get("hidden") or typ in ("Data", "Output", "Action", "Outputs"):
        return None
    desc = _option_title(opt)
    perm = opt.get("permitted") or opt.get("suggested")
    if perm and typ in ("Variable", "Variables", "Pairs", "Terms"):
        desc += f" (measure types: {', '.join(perm)})"

    if typ == "Variable" or typ == "Level":
        s: dict = {"type": ["string", "null"]}
    elif typ == "Variables":
        s = {"type": "array", "items": {"type": "string"}}
    elif typ == "Bool":
        s = {"type": "boolean"}
    elif typ == "List":
        s = {"type": "string", "enum": _list_values(opt)}
    elif typ == "NMXList":
        s = {"type": "array", "items": {"type": "string", "enum": _list_values(opt)}}
    elif typ == "Integer":
        s = {"type": "integer"}
    elif typ == "Number":
        s = {"type": "number"}
    elif typ == "String":
        s = {"type": "string"}
    elif typ == "Terms":
        s = {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
             "description": "list of terms; a term is a list of variable names (interaction)"}
    elif typ == "Pairs":
        s = {"type": "array", "items": {"type": "object", "properties": {
            "i1": {"type": "string"}, "i2": {"type": "string"}}}}
    else:  # Group, Array, ... : free form
        s = {}
    for k_from, k_to in (("min", "minimum"), ("max", "maximum")):
        if k_from in opt and s.get("type") in ("integer", "number"):
            s[k_to] = opt[k_from]
    if "default" in opt and opt["default"] is not None:
        s["default"] = opt["default"]
    if desc:
        s["description"] = (s.get("description", "") + " " + desc).strip()
    return s


def analysis_schema(meta) -> dict:
    props = {}
    for opt in meta.defn.get("options", []):
        name = opt.get("name")
        sch = option_schema(opt)
        if name and sch is not None:
            props[name] = sch
    return {"type": "object", "properties": props}


# ----- model terms the jamovi UI fills in automatically ------------------------

def _is_empty_terms(value) -> bool:
    """True for missing/empty terms: None, [], [[]] (one empty block)."""
    if value is None:
        return True
    if isinstance(value, list):
        return all(_is_empty_terms(v) for v in value) if value and isinstance(value[0], list) else not value
    return False


def fill_model_terms(meta, options: dict) -> list[str]:
    """Fill model terms from the variable lists, as jamovi's UI does.

    In e.g. ``jmv::linReg`` the option ``covs`` only lists the available
    predictors; the model itself is ``blocks``, which the UI fills when
    variables are dropped in. Without it the analysis runs but every table
    stays empty. Same for ``modelTerms`` (ANOVA/ANCOVA). Changes ``options``
    in place and returns the names of the filled options.
    """
    names = {o.get("name") for o in meta.defn.get("options", [])}
    factors = [v for v in options.get("factors") or [] if isinstance(v, str)]
    covs = [v for v in options.get("covs") or [] if isinstance(v, str)]
    filled = []
    if "blocks" in names and _is_empty_terms(options.get("blocks")) and (factors or covs):
        options["blocks"] = [[[v] for v in covs + factors]]
        filled.append("blocks")
    if "modelTerms" in names and _is_empty_terms(options.get("modelTerms")) and (factors or covs):
        terms = []
        for size in range(1, len(factors) + 1):  # full factorial of the factors
            terms += [list(c) for c in itertools.combinations(factors, size)]
        terms += [[c] for c in covs]  # main effects of covariates
        options["modelTerms"] = terms
        filled.append("modelTerms")
    return filled


def results_are_empty(results) -> bool:
    """True if the results contain tables but not a single value."""
    tables = []

    def walk(elem):
        kind = elem.WhichOneof("type")
        if kind == "table":
            tables.append(elem.table)
        elif kind == "group":
            for c in elem.group.elements:
                walk(c)
        elif kind == "array":
            for c in elem.array.elements:
                walk(c)

    walk(results.results)
    if not tables:
        return False
    for t in tables:
        for col in t.columns:
            for cell in col.cells:
                kind = cell.WhichOneof("cellType")
                if kind in ("i", "d") or (kind == "s" and cell.s.strip()):
                    if col.name not in ("var", "term", "model", "name") and not col.name.startswith("."):
                        return False
    return True


# ----- registry ---------------------------------------------------------------

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Awaitable[Any]]
    writes: bool = False
    meta: dict = field(default_factory=dict)

    def openai_spec(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


def _params(props: dict, required: list[str] | None = None) -> dict:
    p = {"type": "object", "properties": {**props, **_INSTANCE}}
    if required:
        p["required"] = required
    return p


class Toolbox:
    """All tools bound to one ``Bridge``."""

    def __init__(self, bridge: Bridge, knowledge=None):
        self.b = bridge
        self.knowledge = knowledge
        self.tools: dict[str, Tool] = {}
        self._register()

    def specs(self) -> list[dict]:
        return [t.openai_spec() for t in self.tools.values()]

    async def call(self, name: str, args: dict | None) -> Any:
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"unknown tool: {name}"}
        try:
            return await tool.func(**(args or {}))
        except BridgeError as e:
            return {"error": str(e)}
        except TypeError as e:
            return {"error": f"bad arguments for {name}: {e}"}

    def _add(self, name, description, parameters, func, writes=False):
        self.tools[name] = Tool(name, description, parameters, func, writes)

    def _register(self) -> None:
        self._add("list_instances",
                  "List the open jamovi windows (data sets) with size and number of analyses.",
                  {"type": "object", "properties": {}}, self.list_instances)
        self._add("get_dataset_schema",
                  "Variables of the data set: name, measure type (nominal/ordinal/continuous/id), "
                  "data type, levels, description, number of rows. Call this first.",
                  _params({}), self.get_dataset_schema)
        self._add("describe_variables",
                  "Summary statistics per variable without raw data: n, missing, mean, sd, min, "
                  "median, max for numbers; level counts for factors.",
                  _params({"variables": {"type": "array", "items": {"type": "string"}}},
                          ["variables"]), self.describe_variables)
        self._add("get_data",
                  "Raw rows of selected variables (only if enabled by the administrator; "
                  f"at most {MAX_DATA_ROWS} rows).",
                  _params({"variables": {"type": "array", "items": {"type": "string"}},
                           "start": {"type": "integer", "default": 0},
                           "count": {"type": "integer", "default": 20}}, ["variables"]),
                  self.get_data)
        self._add("search_analyses",
                  "Search the installed jamovi analyses (title, menu, name). Returns ns and name "
                  "needed for get_analysis_schema and create_analysis.",
                  {"type": "object", "properties": {"query": {
                      "type": "string", "description": "words to look for; empty = all"}}},
                  self.search_analyses)
        self._add("get_analysis_schema",
                  "Options of an analysis as JSON schema (variables, checkboxes, lists). "
                  "Always check this before create_analysis.",
                  {"type": "object", "properties": {"ns": {"type": "string"}, "name": {"type": "string"}},
                   "required": ["ns", "name"]}, self.get_analysis_schema)
        self._add("list_session_analyses",
                  "Analyses currently in the results panel, with id, type and options.",
                  _params({}), self.list_session_analyses)
        self._add("create_analysis",
                  "Create and run an analysis in the user's results panel; waits for it and returns "
                  "its results as Markdown. Only set options you need; others keep defaults.",
                  _params({"ns": {"type": "string", "description": "module, e.g. jmv"},
                           "name": {"type": "string", "description": "analysis, e.g. ttestIS"},
                           "options": {"type": "object", "description": "option name -> value"}},
                          ["ns", "name", "options"]), self.create_analysis, writes=True)
        self._add("update_analysis",
                  "Change options of an existing analysis, re-run it and return the new results.",
                  _params({"analysis_id": {"type": "integer"},
                           "options": {"type": "object"}}, ["analysis_id", "options"]),
                  self.update_analysis, writes=True)
        if self.knowledge is not None:
            self._add("search_docs",
                      "Search the local documentation (jamovi manual, statistics texts, local notes) "
                      "and the descriptions of all installed analyses. Use for questions about "
                      "methods, assumptions, interpretation or how to do something in jamovi.",
                      {"type": "object", "properties": {
                          "query": {"type": "string", "description": "keywords, German or English"},
                          "source": {"type": "string", "description": "optional: limit to one source "
                                     "(e.g. 'analyses'); see the documentation list in the instructions"},
                          "limit": {"type": "integer", "default": 6}},
                       "required": ["query"]}, self.search_docs)
            self._add("read_doc",
                      "Read a documentation section by the id from search_docs (or a whole file "
                      "by its path). Cite title and source when you use it.",
                      {"type": "object", "properties": {"id": {"type": "string"}},
                       "required": ["id"]}, self.read_doc)
            self._add("explain_analysis",
                      "What an analysis is for and what each of its options means, in prose.",
                      {"type": "object", "properties": {"ns": {"type": "string"}, "name": {"type": "string"}},
                       "required": ["ns", "name"]}, self.explain_analysis)
        self._add("get_results",
                  "Results of an existing analysis as Markdown (tables, notes, warnings).",
                  _params({"analysis_id": {"type": "integer"}}, ["analysis_id"]), self.get_results)

    # ----- implementations ------------------------------------------------

    async def list_instances(self) -> Any:
        return [self.b.instance_summary(i) for i in self.b.instances()]

    async def get_dataset_schema(self, instance_id: str | None = None) -> Any:
        inst = self.b.instance(instance_id)
        return {"instance_id": inst.id, "rows": self.b.row_count(inst),
                "variables": [c.as_dict() for c in self.b.columns(inst)]}

    async def describe_variables(self, variables: list[str], instance_id: str | None = None) -> Any:
        inst = self.b.instance(instance_id)
        info = {c.name: c for c in self.b.columns(inst)}
        out = {}
        for name in variables[:30]:
            if name not in info:
                out[name] = {"error": "no such variable"}
                continue
            vals = self.b.values(inst, name)
            out[name] = describe(vals, info[name].measure_type)
        return out

    async def get_data(self, variables: list[str], start: int = 0, count: int = 20,
                       instance_id: str | None = None) -> Any:
        if not _raw_data_allowed():
            return {"error": "raw data access is disabled; use describe_variables"}
        inst = self.b.instance(instance_id)
        count = max(0, min(int(count), MAX_DATA_ROWS))
        cols = {n: self.b.values(inst, n, int(start), count) for n in variables[:20]}
        n = max((len(v) for v in cols.values()), default=0)
        rows = [{k: (v[i] if i < len(v) else None) for k, v in cols.items()} for i in range(n)]
        return {"start": start, "rows": rows}

    async def search_analyses(self, query: str = "") -> Any:
        """Analyses whose title/menu match, or that offer a matching option.

        A test that jamovi provides as an option of another analysis (e.g.
        Mann-Whitney U inside the independent samples t-test) is found via
        the option titles; ``via_options`` names those options.
        """
        words = [w for w in re.split(r"[\s\-_/]+", query.lower()) if w]
        hits = []
        for module, a in self.b.catalogue():
            if not getattr(a, "in_menu", True):
                continue
            fields = [getattr(a, f, "") or "" for f in
                      ("name", "title", "menuGroup", "menuSubgroup", "menuTitle", "menuSubtitle")]
            head = " ".join(map(str, fields + [module.title])).lower()
            hit = {"ns": a.ns, "name": a.name, "title": a.title,
                   "menu": " / ".join(x for x in fields[2:4] if x)}
            if not words:
                hits.append((0, hit))
                continue
            if all(w in head for w in words):
                hits.append((2, hit))
                continue
            opts = []
            for opt in (getattr(a, "defn", {}) or {}).get("options", []):
                text = f"{opt.get('name', '')} {opt.get('title', '')}".lower()
                if all(w in text for w in words):
                    opts.append(opt.get("name"))
            if opts:
                hits.append((1, {**hit, "via_options": opts}))
        hits.sort(key=lambda h: -h[0])
        out = {"count": len(hits), "analyses": [h for _, h in hits[:60]]}
        if not hits and self.knowledge is not None:
            out["hint"] = "nothing found by name; try search_docs for the method"
        return out

    async def get_analysis_schema(self, ns: str, name: str) -> Any:
        meta = self.b.analysis_meta(ns, name)
        return {"ns": ns, "name": name, "title": meta.title, "options": analysis_schema(meta)}

    async def list_session_analyses(self, instance_id: str | None = None) -> Any:
        inst = self.b.instance(instance_id)
        out = []
        for a in self.b.analyses(inst):
            opts = {k: v for k, v in self.b.options_dict(a).items() if v not in (None, [], "", False)}
            out.append({"analysis_id": a.id, "ns": a.ns, "name": a.name,
                        "title": getattr(a.results, "title", "") if a.results else "",
                        "status": a.status.name.lower(), "options": opts})
        return out

    async def _finish(self, inst, analysis, filled: list[str] | None = None) -> Any:
        status = await self.b.wait_complete(analysis)
        out = {"analysis_id": analysis.id, "status": status.lower(), "results": ""}
        if filled:
            out["note"] = (f"filled {', '.join(filled)} from the variables, as the jamovi UI does; "
                           "pass them explicitly for another model")
        if analysis.results is not None and status == "COMPLETE" and results_are_empty(analysis.results):
            out["warning"] = ("the analysis finished but its tables are empty: a required option is "
                              "probably missing (e.g. the model terms such as blocks/modelTerms). "
                              "Check get_analysis_schema; this is not a display problem.")
        if analysis.results is not None:
            out["results"] = _truncate(analysis_to_markdown(analysis.results))
            refs = references(analysis.results)
            if refs:
                out["references"] = refs
        return out

    async def search_docs(self, query: str, source: str | None = None, limit: int = 6) -> Any:
        hits = self.knowledge.index.search(query, source or None, max(1, min(int(limit), 15)))
        return {"count": len(hits), "results": hits}

    async def read_doc(self, id: str) -> Any:
        return self.knowledge.read(id)

    async def explain_analysis(self, ns: str, name: str) -> Any:
        return self.knowledge.explain_analysis(ns, name)

    async def create_analysis(self, ns: str, name: str, options: dict | None = None,
                              instance_id: str | None = None) -> Any:
        inst = self.b.instance(instance_id)
        meta = self.b.analysis_meta(ns, name)
        known = {o.get("name") for o in meta.defn.get("options", [])}
        options = options or {}
        unknown = sorted(set(options) - known)
        if unknown:
            return {"error": f"unknown options for {ns}::{name}: {unknown}; see get_analysis_schema"}
        missing = self._missing_variables(inst, meta, options)
        if missing:
            return {"error": f"no such variables: {missing}"}
        filled = fill_model_terms(meta, options)
        analysis = self.b.create_analysis(inst, ns, name, options)
        return await self._finish(inst, analysis, filled)

    async def update_analysis(self, analysis_id: int, options: dict,
                              instance_id: str | None = None) -> Any:
        inst = self.b.instance(instance_id)
        current = self.b.analysis(inst, analysis_id)
        missing = self._missing_variables(inst, self.b.analysis_meta(current.ns, current.name), options)
        if missing:
            return {"error": f"no such variables: {missing}"}
        merged = {**self.b.options_dict(current), **options}
        filled = fill_model_terms(self.b.analysis_meta(current.ns, current.name), merged)
        for name in filled:
            options[name] = merged[name]
        analysis = self.b.update_analysis(inst, analysis_id, options)
        return await self._finish(inst, analysis, filled)

    async def get_results(self, analysis_id: int, instance_id: str | None = None) -> Any:
        inst = self.b.instance(instance_id)
        analysis = self.b.analysis(inst, analysis_id)
        return await self._finish(inst, analysis)

    def _missing_variables(self, inst, meta, options: dict) -> list[str]:
        """Variable names used in ``options`` that are not in the data set."""
        var_types = {"Variable", "Variables", "Terms", "Pairs"}
        var_opts = {o.get("name") for o in meta.defn.get("options", []) if o.get("type") in var_types}
        names = {c.name for c in self.b.columns(inst)}
        used: set[str] = set()

        def collect(v):
            if isinstance(v, str):
                used.add(v)
            elif isinstance(v, list):
                for x in v:
                    collect(x)
            elif isinstance(v, dict):
                for x in v.values():
                    collect(x)

        for key, value in options.items():
            if key in var_opts:
                collect(value)
        return sorted(u for u in used if u not in names)


def describe(values: list, measure_type: str) -> dict:
    """Summary of one variable's values (None = missing)."""
    present = [v for v in values if v is not None]
    out: dict[str, Any] = {"n": len(present), "missing": len(values) - len(present)}
    nums = [v for v in present if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if measure_type == "continuous" and nums:
        out.update({
            "mean": round(statistics.fmean(nums), 4),
            "sd": round(statistics.stdev(nums), 4) if len(nums) > 1 else None,
            "min": min(nums), "median": statistics.median(nums), "max": max(nums),
        })
    else:
        counts = Counter(str(v) for v in present)
        out["levels"] = dict(counts.most_common(15))
        if len(counts) > 15:
            out["other_levels"] = len(counts) - 15
    return out
