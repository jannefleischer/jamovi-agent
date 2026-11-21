# jamovi_agent

An LLM agent that works inside a running jamovi 28.x server: it inspects the
open data set, creates and runs analyses that show up live in the user's
browser, and interprets the results. It talks to any OpenAI-compatible chat
completions endpoint (e.g. LiteLLM, vLLM, Ollama, OpenAI).

Developed for running jamovi behind JupyterHub, but it has no dependency on
JupyterHub.

## How it hooks into jamovi

jamovi 28 looks for an optional module `jamovi.server.extras` and, if present,
mounts the routes returned by `get_extra_handlers(session)` into its main web
app. The image installs a one-line shim (`shim/extras.py`) at that place,
which re-exports `jamovi_agent.extras`. jamovi itself is not modified.

The agent runs **in the jamovi server process**. That is deliberate: a jamovi
instance accepts exactly one client connection (`Instance.set_coms` replaces
the previous one), so an external client would kick out the browser.
Analyses created server side get odd ids, which the browser accepts as new
analyses and renders.

The module also has to provide `instance_for_open(session, options)`; it keeps
jamovi's default behaviour (open into a new instance).

## Layout

| Module | Purpose |
|---|---|
| `extras.py` | jamovi hook: routes + `instance_for_open` |
| `bridge.py` | thin layer over jamovi internals (instances, data set, analyses) |
| `results.py` | jamovi results protobuf → Markdown for the LLM |
| `tools.py` | tool registry: JSON schemas + implementations on top of `bridge` |
| `llm.py` | minimal async client for `/chat/completions` (aiohttp, no SDK) |
| `agent.py` | tool-calling loop with step/time limits, per-instance history |
| `web.py` | HTTP API under `<jamovi root>/agent/` |
| `docs.py` | local documentation index (Markdown sections + analysis descriptions, BM25) |
| `prompt.py` | system prompt: file or built-in default, placeholders |
| `knowledge.py` | builds index and prompt lazily; `search_docs` / `read_doc` / `explain_analysis` |

No third-party dependencies beyond what jamovi already ships (`aiohttp`,
`protobuf`).

## HTTP API

All paths are relative to the jamovi root (behind JupyterHub:
`/user/<name>/jamovi/`).

| Method | Path | Purpose |
|---|---|---|
| GET | `agent/health` | config check (no secrets) |
| GET | `agent/instances` | open instances with data set summary |
| GET | `agent/tools` | tool schemas as sent to the LLM |
| POST | `agent/tools/{name}` | call one tool directly (JSON body = arguments) |
| POST | `agent/chat` | `{"message": "...", "instance_id": optional}` → Server-Sent Events |
| GET | `agent/prompt` | the rendered system prompt |
| GET | `agent/history` | chat history of an instance |
| POST | `agent/reset` | clear the history |

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `JAMOVI_AGENT_ENABLED` | `true` | `false` mounts only `agent/health` and `agent/static/*` |
| `JAMOVI_AGENT_UI` | `module` | chat UI: `module`, `sidebar`, `both`, `off` |
| `OPENAI_BASE_URL` | – | e.g. `https://litellm.example/v1` |
| `OPENAI_API_KEY` | – | key for the endpoint |
| `JAMOVI_AGENT_MODEL` | `gpt-4o-mini` | model name at the endpoint |
| `JAMOVI_AGENT_MAX_STEPS` | `12` | tool calls per user message |
| `JAMOVI_AGENT_TIMEOUT` | `300` | seconds per user message |
| `JAMOVI_AGENT_ALLOW_RAW_DATA` | `false` | allow `get_data` to return raw rows |
| `JAMOVI_AGENT_LLM_USER` | – | user name/e-mail sent to the endpoint for usage accounting (fallback: `ILS_LLM_USER`) |
| `JAMOVI_AGENT_USER_HEADER` | `X-OpenWebUI-User-Email` | header that carries that user |
| `JAMOVI_AGENT_DOCS` | `/opt/jamovi-agent/docs` | documentation directory (Markdown) |
| `JAMOVI_AGENT_PROMPT_FILE` | – | system prompt file; default `<docs>/system-prompt.md`, then built-in |
| `JAMOVI_AGENT_USER_AGENT` | `jamovi-assistant` | User-Agent sent to the endpoint |

## Chat UI

Two variants, chosen with `JAMOVI_AGENT_UI` (details in `AGENTS.md`):

- `module` (default): the jamovi module **jamovi-assistant** (`module/jamoviassistant`)
  adds the analysis *Assistant*; the chat sits in the results panel and the
  conversation is saved with the `.omv` file.
- `sidebar`: a chat panel next to the jamovi window (button bottom right); the
  conversation is kept in memory only.
- `both`, `off`.

## Documentation and system prompt

The agent searches a local documentation directory and the descriptions of
all installed analyses (from the module definitions jamovi has loaded).

```
docs/
  system-prompt.md          optional; replaces the built-in prompt
  handbook/filters.md       one Markdown file per chapter or topic
  statistik/tests.md
```

Each file may start with front matter:

```
---
title: Filters
source: handbook          # default: first directory name
license: CC BY-SA 4.0
url: https://…
---
```

Files are split into sections at headings (`#`–`###`); `search_docs`
returns section ids (`handbook/filters.md#2`), `read_doc` reads a section or
a whole file. Placeholders in the prompt: `{{docs_index}}`, `{{modules}}`,
`{{jamovi_version}}`. Results of analyses include their references.

## Development

```sh
python3 -m unittest discover -s tests          # unit tests, no jamovi needed
scripts/smoke.sh <image>                        # end-to-end in the jamovi image
```

Pinned against jamovi **28.18**; internals used are listed in `bridge.py`.
