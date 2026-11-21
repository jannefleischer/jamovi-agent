# AGENTS.md – working on jamovi-assistant (jamovi_agent)

Notes for people and coding agents working on this repository. User-facing
documentation is in `README.md`.

## What this is

An LLM assistant that runs **inside the jamovi 28 server process**. It inspects
the open data set, creates/changes analyses that appear live in the user's
browser, reads results and local documentation, and talks to any
OpenAI-compatible chat completions endpoint. The same tools are offered over
MCP. Two chat UIs exist (see below).

Neutral naming: the product is **jamovi-assistant**, chat replies are rendered
as **jamovi-bot** messages. Do not put organisation-specific names or defaults
into this repository; deployment specifics belong to the deploying stack
(env variables, docs folder, system prompt file).

## Layout

| Path | Purpose |
|---|---|
| `src/jamovi_agent/extras.py` | jamovi hook (`jamovi.server.extras`): routes + `instance_for_open` |
| `src/jamovi_agent/bridge.py` | **all** access to jamovi internals; pinned to jamovi 28.18 |
| `src/jamovi_agent/tools.py` | tool registry (JSON schemas, implementations) |
| `src/jamovi_agent/results.py` | results protobuf → Markdown (fold/transpose like the client) |
| `src/jamovi_agent/agent.py` | tool-calling loop, per-instance history |
| `src/jamovi_agent/llm.py` | aiohttp client for `/chat/completions` |
| `src/jamovi_agent/docs.py`, `prompt.py`, `knowledge.py` | local docs index (BM25), system prompt |
| `src/jamovi_agent/mcp.py` | MCP server (Streamable HTTP, JSON responses) |
| `src/jamovi_agent/web.py` | HTTP API under `<jamovi root>/agent/`, UI mode, conversation persistence |
| `src/jamovi_agent/static/` | chat widget (`chat.js`, `chat.css`) and sidebar loader (`sidebar.js`) |
| `module/jamoviassistant/` | jamovi module **jamovi-assistant** (R + YAML), hosts the chat in the results |
| `shim/extras.py` | one-liner installed as `jamovi/server/extras.py` |
| `scripts/smoke.py`, `scripts/smoke.sh` | end-to-end test in a jamovi container |
| `tests/` | unit tests, run without jamovi |

## Configuration (environment of the jamovi process)

| Variable | Default | Meaning |
|---|---|---|
| `JAMOVI_AGENT_ENABLED` | `true` | `false`: only `agent/health` and `agent/static/*` |
| `JAMOVI_AGENT_UI` | `module` | chat UI: `module` (results panel, saved in the .omv), `sidebar` (panel next to the window), `both`, `off` |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY` | – | endpoint (OpenAI-compatible, e.g. LiteLLM, vLLM, Ollama) |
| `JAMOVI_AGENT_MODEL` | `gpt-4o-mini` | model; must support tool calling |
| `JAMOVI_AGENT_MAX_STEPS` / `_TIMEOUT` | `12` / `300` | limits per user message |
| `JAMOVI_AGENT_ALLOW_RAW_DATA` | `false` | allow `get_data` (raw rows to the LLM) |
| `JAMOVI_AGENT_DOCS` | `/opt/jamovi-agent/docs` | Markdown docs folder; `system-prompt.md` inside replaces the built-in prompt |
| `JAMOVI_AGENT_PROMPT_FILE` | – | explicit system prompt file |
| `JAMOVI_AGENT_LLM_USER` | – | user sent to the endpoint for accounting (fallback `ILS_LLM_USER`) |
| `JAMOVI_AGENT_USER_HEADER` | `X-OpenWebUI-User-Email` | header for that user |
| `JAMOVI_AGENT_USER_AGENT` | `jamovi-assistant` | User-Agent sent to the endpoint |

In the JupyterHub stack these come from `secrets/jamovi.env` (passed by the
spawner); the docs folder is `jupyterconf/jamovi/agent-docs/` mounted read-only.

## The two chat UIs

Both use the same widget (`static/chat.js`, `JamoviAssistant.mount()`), served
by the agent at `agent/static/`, and the same API (`history`, `chat` as SSE,
`reset`).

1. **Module (default, `JAMOVI_AGENT_UI=module`)** – the user adds the analysis
   *Assistant* (module `jamoviassistant`, analysis `assistant`). Its Html
   result loads `inst/loader.js` (served by jamovi from `R/jamoviassistant/`),
   which loads the widget from `../../../agent/` (results frame:
   `<root>/r/<instance>/<analysis>/`). The conversation is written by the
   server into the hidden String option `chat` (JSON list of `{role, content}`)
   after each turn, so it is **saved with the .omv**; on the first `history`
   or `chat` request after opening a file it is restored from there.
2. **Sidebar (`sidebar` or `both`)** – `index.html` always loads
   `../agent/static/sidebar.js` (patched in at image build). The script asks
   `agent/health` and only shows the button/panel if the mode allows it. Not
   saved in the file; the history lives in memory per instance.

## Invariants – read before changing jamovi-facing code

- **One client connection per instance.** `Instance.set_coms` replaces the
  previous connection. Never connect a second websocket to an instance; the
  agent works in-process through `bridge.py`.
- **Server-created analyses have odd ids** (`Analyses.create(0, …)`); the
  browser adopts unknown odd ids from an `AnalysisResponse`. Client-created ones
  are even.
- **Revision on option changes:** the client adopts options from the server only
  if `response.revision == analysis.revision`. `Bridge.update_analysis` therefore
  keeps the revision. Bumping it makes the browser keep (and later send back)
  stale options – this would e.g. overwrite the saved conversation.
- **The assistant analysis is not an analysis for the agent:** `ns ==
  "jamoviassistant"` is excluded from the catalogue and from
  `list_session_analyses`.
- **CSP and sandbox:** `connect-src 'self'`; the results frame is sandboxed with
  `allow-scripts allow-same-origin` only. All UI requests go to the agent routes
  in the same origin (no external URLs), and **no `<form>`**: without
  `allow-forms` the browser blocks submission and the `submit` event never fires.
  Use buttons with click handlers.
- **Streaming through jupyter-server-proxy:** it buffers responses unless the
  request has exactly `Accept: text/event-stream`; `chat.js` sends it, so
  progress events arrive while the agent works.
- **Paths behind a prefix:** jamovi runs under `/user/<name>/jamovi/…`. Never
  use root-absolute URLs (`/agent/…`); resolve relative to the page
  (`new URL('../agent/', location.href)`). The stack patches jamovi's own client
  for the same reason (see the stack's `Dockerfile.jamovi` and
  `jamovi-upstream-issue.md`).
- **No third-party Python packages:** jamovi's Python has no pip. Use the
  standard library plus what jamovi ships (`aiohttp`, `protobuf`, `yaml`).
  Import `aiohttp` lazily where unit tests need to run without it.
- **Never break jamovi:** `extras.get_extra_handlers` swallows exceptions and
  returns no routes; `health` must always answer.

## Build

- Module: compiled with `jmc` from jamovi's build stage `compiler`:
  `docker build --target compiler -f docker/jamovi-Dockerfile -t jamovi/jamovi-compiler:<version> <jamovi source>`.
  The stack's `Dockerfile.jamovi` has a stage `assistant-module` that runs
  `jmc --install` and copies the result to `/usr/lib/jamovi/modules/jamoviassistant`
  (system module, no signature check).
- Package and widget: copied to `/usr/lib/jamovi/server/jamovi_agent`; shim to
  `/usr/lib/jamovi/server/jamovi/server/extras.py`.

## Tests

```sh
python3 -m unittest discover -s tests                       # no jamovi needed
scripts/smoke.sh [container] [--chat "question"]           # end-to-end
```

`smoke.sh` copies the working tree into a running test container (jamovi image
started with `jupyter server --IdentityProvider.token=t`, base url
`/user/test/`), restarts it and runs `smoke.py`, which acts like the browser
(one websocket per instance) and checks tools, MCP, static files, the module
and – with `--chat` – a real LLM turn and the saved conversation.

Gotchas: restart the **container**, not the jamovi process (jupyter-server-proxy
does not reliably restart a killed jamovi); run `smoke.py` with
`PYTHONPATH=/usr/lib/jamovi/server` and **without** `python -I` (that ignores
`PYTHONPATH`).

## On a jamovi update

1. Diff the internals listed at the top of `bridge.py`, `client/main/instance.ts`
   (`AnalysisResponse` handling) and `server.py` (`extras` hook, CSP, routes).
2. Rebuild the compiler image and the module.
3. Run unit tests and `smoke.sh --chat`.

## Conventions

- Everything in English: code, comments, docstrings, commit messages, UI texts
  (`TEXT` in `chat.js`, sidebar, module titles and labels). Deployments can
  answer in another language through their system prompt.
- Commit subjects short and imperative; one topic per commit.
