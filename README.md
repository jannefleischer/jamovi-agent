# jamovi_agent

An LLM agent that works inside a running jamovi 28.x server: it inspects the
open data set, creates and runs analyses that show up live in the user's
browser, and interprets the results. It talks to any OpenAI-compatible chat
completions endpoint (at ILS: LiteLLM).

Built for the ILS JupyterHub stack (`../Dockerfile.jamovi`), but it has no
dependency on JupyterHub.

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
| GET | `agent/history` | chat history of an instance |
| POST | `agent/reset` | clear the history |

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `JAMOVI_AGENT_ENABLED` | `true` | `false` mounts only `agent/health` |
| `OPENAI_BASE_URL` | – | e.g. `https://litellm.example/v1` |
| `OPENAI_API_KEY` | – | key for the endpoint |
| `JAMOVI_AGENT_MODEL` | `gpt-4o-mini` | model name at the endpoint |
| `JAMOVI_AGENT_MAX_STEPS` | `12` | tool calls per user message |
| `JAMOVI_AGENT_TIMEOUT` | `300` | seconds per user message |
| `JAMOVI_AGENT_ALLOW_RAW_DATA` | `false` | allow `get_data` to return raw rows |
| `ILS_LLM_USER` | – | sent as `X-OpenWebUI-User-Email` for usage accounting |

## Development

```sh
python3 -m unittest discover -s tests          # unit tests, no jamovi needed
scripts/smoke.sh <image>                        # end-to-end in the jamovi image
```

Pinned against jamovi **28.18**; internals used are listed in `bridge.py`.
