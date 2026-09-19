# CLAUDE.md

Rules for this repo. Follow these before applying general defaults — they encode
decisions already made for this project, not preferences to re-derive.

## What this is

A POC AI agent that lets a user upload structured data (CSV/XLSX) and query,
transform, and visualize it through natural language, executing agent-written
Python in an isolated sandbox rather than translating everything to SQL.

## Repo layout (uv workspace)

```
apps/api/        FastAPI backend (package name: api)
apps/worker/      async job runner (added later)
packages/core/     Settings, DB/S3/Redis client factories — the ONLY place
                   connection details are read from
packages/agent/    LangGraph graphs + the OpenRouter LLM wrapper
packages/sandbox/  isolated code-execution engine (Phase 2+)
packages/tools/    profiler, chart-spec, join, versioning tools (Phase 1+)
scripts/          one-off scripts (smoke tests, etc.), run from repo root
```

Each `apps/*` and `packages/*` directory is its own uv workspace member with
its own `pyproject.toml`. The root `pyproject.toml` is a virtual, non-packaged
project (`[tool.uv] package = false`) that depends on every member so a single
`uv sync` at the root installs the whole workspace into one venv.

## Package management — uv only

- Never use `pip`, `poetry`, `conda`, or a manually created `venv`. This project
  has no requirements.txt — dependencies live only in `pyproject.toml` files.
- Add a dependency to a specific package: `uv add <pkg> --package <name>`
  (e.g. `uv add pandas --package tools`). Add a root dev tool:
  `uv add --dev <pkg>`.
- Install/update the whole workspace: `uv sync`.
- Run anything (scripts, the API, a REPL) via `uv run ...` — never invoke a
  bare `python` that isn't `uv run python`, since that bypasses the workspace
  venv.
- Run the API: `uv run uvicorn api.main:app --reload --port 8000` (from repo root).
- If `uv` isn't on PATH in a given shell, fall back to `python -m uv <args>` —
  do not reinstall or shadow it another way.

## Local infra = docker-shaped cloud infra, always

- Postgres, MinIO (S3-compatible), Redis, and Chroma run via
  `docker-compose up` — never installed natively, never SQLite, never a bare
  filesystem folder standing in for object storage.
- All connection info comes from `core.settings.get_settings()`, which reads
  `.env`. Never hardcode a host, port, bucket name, or credential anywhere
  outside `packages/core`. This is what keeps "move to the cloud" a config
  change instead of a rewrite — do not special-case local paths in app code.
- Talk to Postgres only through SQLAlchemy (`core.storage.get_engine`), to
  object storage only through the boto3 S3 client (`core.storage.get_s3_client`),
  never through `open()`/raw filesystem paths for anything that represents a
  dataset or a dataset version.
- `.env` is gitignored and never committed. `.env.example` is the template —
  update it whenever a new setting is added to `core.settings.Settings`.

## LLM provider — OpenRouter via LangGraph, not the Claude Agent SDK

- All model calls go through `agent.llm.get_chat_model()`, which wraps
  `langchain_openai.ChatOpenAI` pointed at OpenRouter
  (`OPENROUTER_BASE_URL`/`OPENROUTER_MODEL`/`OPENROUTER_API_KEY` in `.env`).
  Never instantiate a chat model directly elsewhere — swapping models must
  stay a one-line env change.
- Do not introduce the Claude Agent SDK or the Anthropic SDK into this
  project — it does not support routing to arbitrary OpenRouter models and
  is not the chosen harness here. Agent orchestration is LangGraph only.
- API keys are read from `.env` via `core.settings`, never hardcoded, never
  logged, never included in a prompt or committed to a file.

## Sandbox execution boundary (non-negotiable once Phase 2 lands)

- Any LLM-generated code that touches a dataset executes only inside the
  sandbox executor in `packages/sandbox` — never via a bare `exec()`/`eval()`
  in the API or worker process.
- The sandbox has no network access, resource/time limits, and a mounted
  dataset copy that is read-only unless the operation is an explicit,
  user-confirmed transform.
- Original uploaded files and every dataset version are immutable once
  written. A transform always produces a new version; nothing is patched
  in place. Undo = repointing the "active version" pointer, never a delete.

## Data handling

- Raw row-level data is never sent to the LLM. Only schema, column
  stats/samples, and aggregated results go into prompts.
- Columns flagged as PII during profiling are excluded from prompts and
  from any response text unless the user explicitly requests otherwise
  post-Phase-7 guardrails.
- Destructive operations (column drop, in-place-feeling overwrite, a join
  that changes row count) require an explicit preview + confirmation step
  once that flow exists — never auto-commit a transform.

## Code style for this repo

- No comments explaining *what* code does; a short comment only for a
  non-obvious *why* (e.g. a sandbox security constraint).
- No speculative abstractions ahead of the phase that needs them — an empty
  `packages/sandbox`/`packages/tools` stub is fine per the phased plan, but
  don't build interfaces for features two phases away.
- Match existing package boundaries: don't reach into `packages/agent` from
  `packages/tools` (or similar cross-layer shortcuts) — route through
  `packages/core` if two packages need to share something.

## Before considering any phase "done"

Each phase in the implementation plan has an explicit verification checklist
(sandbox isolation tests, schema-inference accuracy, cost/latency baselines,
etc.). Do not mark a phase complete or start the next one until its checklist
items have actually been run and confirmed — not just implemented.
