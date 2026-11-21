"""Documentation index and system prompt, built lazily on first use.

Lazy because jamovi reads its module library after the extras hook has been
called; the analysis catalogue is only complete once a client connected.
"""
from __future__ import annotations

import logging
import os

from .docs import DocIndex, analysis_text
from .prompt import docs_index_text, jamovi_version, load_prompt, render

log = logging.getLogger(__name__)

DEFAULT_DOCS_DIR = "/opt/jamovi-agent/docs"


class Knowledge:
    """Holds the ``DocIndex`` and the rendered system prompt."""

    def __init__(self, bridge, docs_dir: str | None = None):
        self.bridge = bridge
        self.docs_dir = docs_dir or os.environ.get("JAMOVI_AGENT_DOCS", DEFAULT_DOCS_DIR)
        self._index: DocIndex | None = None
        self._n_analyses = 0
        self._prompt: str | None = None
        self.prompt_source = ""

    @property
    def index(self) -> DocIndex:
        if self._index is None or self._n_analyses == 0:
            self._build()
        return self._index

    def _build(self) -> None:
        index = DocIndex()
        n_files = index.add_markdown_dir(self.docs_dir)
        try:
            self._n_analyses = index.add_analyses(self.bridge.catalogue())
        except Exception:
            log.exception("jamovi_agent: could not index analyses")
            self._n_analyses = 0
        self._index = index
        self._prompt = None
        log.info("jamovi_agent: indexed %d doc files, %d analyses", n_files, self._n_analyses)

    @property
    def system_prompt(self) -> str:
        index = self.index
        if self._prompt is None:
            template, self.prompt_source = load_prompt(self.docs_dir)
            modules = sorted({m.name for m, _ in self.bridge.catalogue()})
            self._prompt = render(template, docs_index=docs_index_text(index.files, self._n_analyses),
                                  modules=modules, version=jamovi_version())
        return self._prompt

    def status(self) -> dict:
        index = self.index
        _ = self.system_prompt
        return {"docs_dir": self.docs_dir, "doc_files": len(index.files),
                "analyses": self._n_analyses, "sections": len(index.docs),
                "prompt_source": self.prompt_source}

    def read(self, doc_id: str, max_chars: int = 8000) -> dict:
        """A section by id, or a whole file (all its sections) by file path."""
        index = self.index
        doc = index.docs.get(doc_id)
        if doc is not None:
            return {"id": doc.id, "source": doc.source, "title": doc.title,
                    "license": doc.meta.get("license", ""), "url": doc.meta.get("url", ""),
                    "text": doc.text[:max_chars]}
        parts = [d for d in index.docs.values() if d.meta.get("file") == doc_id]
        if not parts:
            return {"error": f"no such document: {doc_id}; use an id from search_docs"}
        text = "\n\n".join(d.text for d in parts)
        meta = parts[0].meta
        return {"id": doc_id, "source": parts[0].source, "title": meta.get("file_title", ""),
                "license": meta.get("license", ""), "url": meta.get("url", ""),
                "text": text[:max_chars] + ("\n…(truncated)" if len(text) > max_chars else "")}

    def explain_analysis(self, ns: str, name: str) -> dict:
        """Description of an analysis and its options (references come with results)."""
        meta = self.bridge.analysis_meta(ns, name)
        doc = self.index.docs.get(f"analysis:{meta.ns}/{meta.name}")
        return {"ns": ns, "name": name, "title": meta.title,
                "description": doc.text if doc else analysis_text(meta)}
