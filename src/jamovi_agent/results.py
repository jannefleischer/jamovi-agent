"""Render jamovi results (``ResultsElement`` protobufs) as Markdown text.

The LLM never sees images; it gets tables, notes, notices and preformatted
text. Works on any object with the protobuf API (duck typing), so tests can
use the real message classes or simple stand-ins.
"""
from __future__ import annotations

import html
import math
import re

MAX_ROWS = 60

_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return html.unescape(_TAG.sub(" ", s)).strip()


def format_cell(cell, fmt: str = "") -> str:
    """One table cell as text (numbers with sensible precision)."""
    kind = cell.WhichOneof("cellType")
    if kind == "i":
        text = str(cell.i)
    elif kind == "d":
        d = cell.d
        if math.isnan(d):
            text = "NaN"
        elif "pvalue" in fmt and d < 0.001:
            text = "< .001"
        elif "zto" in fmt or "pvalue" in fmt:
            text = f"{d:.3f}"
        elif d != 0 and (abs(d) >= 1e6 or abs(d) < 1e-3):
            text = f"{d:.3g}"
        else:
            text = f"{d:.3f}"
    elif kind == "s":
        text = cell.s
    elif kind == "o":
        text = "" if cell.o == 0 else "NaN"  # MISSING / NOT_A_NUMBER
    else:
        text = ""
    if cell.symbols:
        text += "".join(cell.symbols)
    return text.replace("|", "\\|")


def _visible(elem) -> bool:
    # Visible enum: DEFAULT_YES=0, DEFAULT_NO=1, YES=2, NO=3
    return int(getattr(elem, "visible", 0)) not in (1, 3)


def table_to_markdown(table, title: str = "") -> str:
    cols = [c for c in table.columns if _visible(c)]
    if not cols:
        return ""
    n_rows = max((len(c.cells) for c in cols), default=0)
    lines = []
    if title:
        lines.append(f"**{title}**")
    header = [(c.superTitle + " " if c.superTitle else "") + (c.title or c.name) for c in cols]
    lines.append("| " + " | ".join(h.strip() for h in header) + " |")
    lines.append("|" + "---|" * len(cols))
    for r in range(min(n_rows, MAX_ROWS)):
        row = []
        for c in cols:
            row.append(format_cell(c.cells[r], c.format) if r < len(c.cells) else "")
        lines.append("| " + " | ".join(row) + " |")
    if n_rows > MAX_ROWS:
        lines.append(f"_({n_rows - MAX_ROWS} more rows not shown)_")
    for note in table.notes:
        if note.note:
            lines.append(f"_Note: {_strip_html(note.note)}_")
    return "\n".join(lines)


def element_to_markdown(elem, depth: int = 0) -> str:
    """Recursively render a results element; empty parts are skipped."""
    if not _visible(elem):
        return ""
    kind = elem.WhichOneof("type")
    title = elem.title
    parts: list[str] = []

    if elem.error.message:
        parts.append(f"> Error{f' in {title}' if title else ''}: {_strip_html(elem.error.message)}")

    if kind == "table":
        parts.append(table_to_markdown(elem.table, title))
    elif kind in ("group", "array"):
        children = elem.group.elements if kind == "group" else elem.array.elements
        inner = [element_to_markdown(c, depth + 1) for c in children]
        inner = [i for i in inner if i]
        if inner:
            if title and depth > 0:
                parts.append("#" * min(depth + 2, 6) + " " + title)
            parts.extend(inner)
    elif kind == "preformatted" and elem.preformatted.strip():
        parts.append((f"**{title}**\n" if title else "") + "```\n" + elem.preformatted.strip() + "\n```")
    elif kind == "text" and elem.text.strip():
        parts.append(elem.text.strip())
    elif kind == "html" and elem.html.content.strip():
        parts.append(_strip_html(elem.html.content))
    elif kind == "notice" and elem.notice.content.strip():
        parts.append(f"> {_strip_html(elem.notice.content)}")
    elif kind in ("image", "svg"):
        parts.append(f"[plot: {title or elem.name}]")

    return "\n\n".join(p for p in parts if p)


def analysis_to_markdown(response) -> str:
    """Render an ``AnalysisResponse`` (analysis.results) as Markdown."""
    results = response.results
    title = results.title or response.title or response.name
    body = element_to_markdown(results, depth=0)
    head = f"## {title} (analysis {response.analysisId}, {response.ns}::{response.name})"
    return head + ("\n\n" + body if body else "\n\n_(no results yet)_")
