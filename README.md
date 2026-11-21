# jamovi-agent

**An AI assistant inside jamovi.** You ask in plain language ("Is there a
difference in tooth length between the two supplements?"), and the assistant
looks at your open data set, picks a suitable analysis, runs it in jamovi so it
appears in your results like any analysis you would have clicked together
yourself, and explains the outcome. You can change or delete those analyses as
usual; the conversation is saved with your `.omv` file.

The assistant needs a large language model (LLM). It works with any service that
offers the OpenAI chat API with tool calling: OpenAI, a LiteLLM or vLLM gateway,
or a local Ollama.

## What you need

- **jamovi 28.x as a server** (the web version, e.g. in Docker), not the desktop
  app. Tested with **28.18**. jamovi-agent uses jamovi's optional hook
  `jamovi.server.extras`, which exists in the 28 series; other versions may need
  adjustments (see `bridge.py`).
- **Path mode**: jamovi must serve its three parts under one address with a
  path, e.g. `JAMOVI_HOST_A=example.org/jamovi`, `JAMOVI_HOST_B=example.org/jamovi/a`,
  `JAMOVI_HOST_C=example.org/jamovi/r`. The chat in the results panel reaches the
  assistant only this way. jamovi's client uses absolute asset paths, which do
  not work in path mode; `docker_example/Dockerfile` contains the small `sed`
  patches that fix this.
- **An LLM endpoint** with tool calling (function calling) and an API key.
  Small local models pick wrong analyses or options more often; test with your
  own data before rolling out.
- Data leaves the server only as far as the assistant needs it: variable names,
  summaries and analysis results go to the LLM, raw rows only if
  `JAMOVI_AGENT_ALLOW_RAW_DATA=true`. Choose the endpoint accordingly.

Official jamovi images on Docker Hub stop at 28.0; build the base image from
the jamovi sources (`docker build -f docker/jamovi-Dockerfile -t
jamovi/jamovi:28.18 .` in a jamovi checkout).

## Installation

### Try it: the Docker example

```sh
ci/build.sh                                   # builds dist/ (module + package)
cp docker_example/.env.example docker_example/.env   # enter endpoint, key, model
docker compose -f docker_example/compose.yaml up --build
```

Open http://localhost:41337/jamovi/, load a data set, then choose **Analyses →
Assistant → Assistant** and type your question in the results panel.
`ci/build.sh` needs R and network access; without a local R run it in a
container: `docker run --rm -v $PWD:/w -w /w rocker/r-ver:4.6.0 ci/build.sh`.

### Into your own jamovi image

A release (GitLab: *Deploy → Releases*) contains

| File | What it is |
|---|---|
| `jamovi-agent-<version>.tar.gz` | the server extension (`src/jamovi_agent`, `shim/extras.py`) |
| `jamoviassistant_<version>.jmo` | the jamovi module with the chat |
| `SHA256SUMS` | checksums |

Three steps in your Dockerfile, plus the path-mode patches (see `docker_example/Dockerfile`):

1. copy `src/jamovi_agent` to `/usr/lib/jamovi/server/jamovi_agent`,
2. copy `shim/extras.py` to `/usr/lib/jamovi/server/jamovi/server/extras.py`,
3. unzip the `.jmo` into `/usr/lib/jamovi/modules/` (then it is installed for
   all users and cannot be removed by them).

Then set at least `OPENAI_BASE_URL`, `OPENAI_API_KEY` and `JAMOVI_AGENT_MODEL`
(all options: [Configuration](#configuration-environment)). Check the setup at
`<jamovi address>/agent/health`: it shows whether the endpoint is configured,
without revealing secrets.

### Releases

The GitLab pipeline (`.gitlab-ci.yml`) runs the unit tests and builds `dist/`
on every push. A tag `vX.Y.Z` additionally uploads the files to the package
registry and creates a release:

```sh
# bump version in pyproject.toml and module/jamoviassistant (DESCRIPTION, jamovi/0000.yaml)
git tag v0.2.0 && git push origin v0.2.0
```

## License

AGPL-3.0 for the server extension, GPL-2.0-or-later for the jamovi module, like
jamovi itself (details in `LICENSE.md`).

---

# Technical details

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
