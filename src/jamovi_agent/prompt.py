"""System prompt: loaded from a file if available, placeholders filled in.

Lookup order: ``JAMOVI_AGENT_PROMPT_FILE``, then ``<docs>/system-prompt.md``,
then ``DEFAULT_PROMPT``. Placeholders:

- ``{{docs_index}}``   available documentation (files and analysis descriptions)
- ``{{jamovi_version}}`` version of the running jamovi
- ``{{modules}}``      installed modules
"""
from __future__ import annotations

import os

DEFAULT_PROMPT = """\
You are a statistics assistant built into jamovi {{jamovi_version}}. You work \
directly in the user's open jamovi window through tools: you can inspect the \
data set and create, change and read analyses. Analyses you create appear in \
the user's results panel.

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
6. For questions about methods, assumptions or how to use jamovi, look it up \
first (search_docs, read_doc, explain_analysis) and name the source. Not \
needed for plain "run this" requests.
7. If the request is unclear or the data do not fit, say so and ask.

Answer in the language of the user (default German). Be concise; use short \
Markdown with tables only where they help.

Installed modules: {{modules}}

Documentation you can search:
{{docs_index}}"""


def jamovi_version() -> str:
    for path in (os.environ.get("JAMOVI_VERSION_PATH", ""), "/usr/lib/jamovi/version"):
        try:
            with open(path, encoding="utf-8") as f:
                v = f.read().strip()
            return ".".join(v.split(".")[:2]) if v else ""
        except OSError:
            continue
    return ""


def load_prompt(docs_dir: str | None) -> tuple[str, str]:
    """(prompt template, where it came from)."""
    candidates = [os.environ.get("JAMOVI_AGENT_PROMPT_FILE", "")]
    if docs_dir:
        candidates.append(os.path.join(docs_dir, "system-prompt.md"))
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read().strip()
                if text:
                    return text, path
            except OSError:
                pass
    return DEFAULT_PROMPT, "builtin"


def docs_index_text(files: list[dict], n_analyses: int) -> str:
    """Overview of the documentation, grouped by source."""
    lines = []
    if n_analyses:
        lines.append(f"- analyses: descriptions of {n_analyses} installed analyses and their "
                     "options (explain_analysis, or search_docs with source='analyses')")
    by_source: dict[str, list[dict]] = {}
    for f in files:
        by_source.setdefault(f["source"], []).append(f)
    for source, items in sorted(by_source.items()):
        titles = "; ".join(f["title"] for f in items[:12])
        more = f" (+{len(items) - 12} more)" if len(items) > 12 else ""
        lines.append(f"- {source}: {titles}{more}")
    return "\n".join(lines) if lines else "- (no documentation installed)"


def render(template: str, *, docs_index: str, modules: list[str], version: str) -> str:
    return (template
            .replace("{{docs_index}}", docs_index)
            .replace("{{modules}}", ", ".join(modules) or "-")
            .replace("{{jamovi_version}}", version or ""))
