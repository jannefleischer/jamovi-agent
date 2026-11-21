"""Local documentation for the agent: a small full-text index (BM25).

Two kinds of entries:

- Markdown files from a docs directory (``JAMOVI_AGENT_DOCS``), split into
  sections at headings. Optional front matter (``title``, ``source``,
  ``license``, ``url``, ``topics``) between ``---`` lines.
- Descriptions of the installed analyses and their options, taken from the
  module definitions jamovi has loaded (``jamovi-full.yaml``), so they need
  no maintenance.

Pure Python, no extra packages; the index is built once and kept in memory.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

SKIP_FILES = {"system-prompt.md", "readme.md"}
MAX_SECTION = 6000  # characters per indexed section
_WORD = re.compile(r"\w+", re.UNICODE)
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$", re.M)


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) > 1]


@dataclass
class Doc:
    """One searchable unit (a Markdown section or one analysis)."""

    id: str
    source: str
    title: str
    text: str
    meta: dict = field(default_factory=dict)

    def snippet(self, terms: Iterable[str], width: int = 300) -> str:
        low = self.text.lower()
        pos = min((p for p in (low.find(t) for t in terms) if p >= 0), default=0)
        start = max(0, pos - width // 3)
        s = self.text[start:start + width].replace("\n", " ").strip()
        return ("…" if start else "") + s + ("…" if start + width < len(self.text) else "")


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Split ``---`` front matter (simple ``key: value`` lines) from the body."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("'\"")
    return meta, text[end + 4:].lstrip("\n")


def split_sections(body: str, file_title: str) -> list[tuple[str, str]]:
    """(title, text) per heading; text before the first heading belongs to the file."""
    marks = [(m.start(), m.group(2).strip()) for m in _HEADING.finditer(body)]
    if not marks:
        return [(file_title, body.strip())]
    out = []
    if body[:marks[0][0]].strip():
        out.append((file_title, body[:marks[0][0]].strip()))
    for i, (pos, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        text = body[pos:end].strip()
        if len(text) > len(title) + 6:  # skip empty headings
            out.append((title, text))
    return out


def _description(value) -> str:
    if isinstance(value, dict):
        return str(value.get("main") or value.get("ui") or value.get("R") or "")
    return str(value or "")


def analysis_text(meta) -> str:
    """Plain-text description of an analysis and its options."""
    defn = getattr(meta, "defn", {}) or {}
    lines = [f"{meta.title} ({meta.ns}::{meta.name})"]
    menu = " / ".join(x for x in (meta.menuGroup, meta.menuSubgroup) if x)
    if menu:
        lines.append(f"Menu: {menu}")
    desc = _description(defn.get("description")).strip()
    if desc:
        lines.append(desc)
    for opt in defn.get("options", []):
        if opt.get("hidden") or opt.get("type") in ("Data", "Output", "Action"):
            continue
        d = _description(opt.get("description")).strip().replace("\n", " ")
        title = opt.get("title") or ""
        lines.append(f"- {opt.get('name')}" + (f" ({title})" if title else "") + (f": {d}" if d else ""))
    return "\n".join(lines)


class DocIndex:
    """BM25 index over ``Doc`` entries."""

    k1 = 1.5
    b = 0.75

    def __init__(self):
        self.docs: dict[str, Doc] = {}
        self.files: list[dict] = []  # one entry per Markdown file, for the prompt
        self._tf: dict[str, Counter] = {}
        self._df: Counter = Counter()
        self._len: dict[str, int] = {}

    def add(self, doc: Doc) -> None:
        tokens = tokenize(doc.title + " " + doc.text)
        self.docs[doc.id] = doc
        self._tf[doc.id] = Counter(tokens)
        self._len[doc.id] = len(tokens)
        self._df.update(set(tokens))

    def add_markdown_dir(self, root: str) -> int:
        """Index all ``*.md`` below ``root``; returns the number of files."""
        n = 0
        if not root or not os.path.isdir(root):
            return 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if not name.endswith(".md") or name.lower() in SKIP_FILES:
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                try:
                    with open(path, encoding="utf-8") as f:
                        meta, body = parse_front_matter(f.read())
                except (OSError, UnicodeDecodeError):
                    continue
                first = re.search(r"^#\s+(.*)$", body, re.M)
                title = meta.get("title") or (first.group(1).strip() if first else name[:-3])
                source = meta.get("source") or (rel.split("/")[0] if "/" in rel else "docs")
                self.files.append({"path": rel, "title": title, "source": source,
                                   "license": meta.get("license", ""), "topics": meta.get("topics", "")})
                for i, (sec_title, text) in enumerate(split_sections(body, title)):
                    self.add(Doc(id=f"{rel}#{i}", source=source, title=sec_title,
                                 text=text[:MAX_SECTION],
                                 meta={"file": rel, "file_title": title, **meta}))
                n += 1
        return n

    def add_analyses(self, catalogue: Iterable) -> int:
        """Index (module, analysis_meta) pairs from ``Bridge.catalogue()``."""
        n = 0
        for module, meta in catalogue:
            self.add(Doc(id=f"analysis:{meta.ns}/{meta.name}", source="analyses",
                         title=f"{meta.title} ({meta.ns}::{meta.name})", text=analysis_text(meta),
                         meta={"ns": meta.ns, "name": meta.name, "module": module.title}))
            n += 1
        return n

    def sources(self) -> list[str]:
        return sorted({d.source for d in self.docs.values()})

    def search(self, query: str, source: str | None = None, limit: int = 8) -> list[dict]:
        terms = tokenize(query)
        if not terms or not self.docs:
            return []
        n_docs = len(self.docs)
        avg = sum(self._len.values()) / n_docs
        scores: dict[str, float] = {}
        for doc_id, tf in self._tf.items():
            if source and self.docs[doc_id].source != source:
                continue
            score = 0.0
            for t in terms:
                f = tf.get(t)
                if not f:
                    continue
                idf = math.log(1 + (n_docs - self._df[t] + 0.5) / (self._df[t] + 0.5))
                norm = f + self.k1 * (1 - self.b + self.b * self._len[doc_id] / avg)
                score += idf * f * (self.k1 + 1) / norm
            if score > 0:
                scores[doc_id] = score
        best = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return [{"id": i, "source": self.docs[i].source, "title": self.docs[i].title,
                 "score": round(s, 2), "snippet": self.docs[i].snippet(terms)} for i, s in best]
