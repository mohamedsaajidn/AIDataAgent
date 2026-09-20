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
packages/core/     Settings, DB/S3/Redis client factories, and the catalog
                   ORM models (Dataset/DatasetVersion/Column/ChatSession/
                   ChatMessage) — the ONLY place connection details are read from
packages/agent/    LangGraph graphs, the OpenRouter LLM wrapper, and any
                   other LLM-calling logic. `schema_context.py` renders
                   catalog metadata (names/types/descriptions/distinct
                   counts/bounded sample values) into prompt text — this is
                   the ONLY dataset-derived content that ever reaches an LLM
                   prompt; PII-flagged columns get a warning instead of
                   sample values. `graphs/qa.py` is the read-only Q&A agent:
                   plan (routes sql/pandas/clarify) → generate_code → execute
                   (calls sandbox.run_python) → verify (heuristic result-type
                   check) → respond, with a retry edge feeding the traceback
                   back into generate_code (bounded by MAX_ATTEMPTS).
                   `plan`'s sql/pandas choice is only a hint fed into the
                   generate_code prompt - it is NEVER enforced, and the model
                   often ignores it (e.g. writes plain pandas for a question
                   the router guessed was SQL-shaped). The `route` value
                   returned to callers is therefore derived from the accepted
                   code itself (`_effective_route` - `"sql"` iff `"duckdb"`
                   appears in the code), not from `plan`'s pre-code guess.
                   Don't reuse `state["route"]` directly as a "what approach
                   did it use" signal anywhere - always go through
                   `_effective_route`.
packages/sandbox/  isolated code-execution engine. `executor.py` runs
                   arbitrary Python in an ephemeral, hardened Docker
                   container (no network, read-only root FS, memory/CPU/pids
                   limits); `blocklist.py` is a static AST pre-check (defense
                   in depth, not the real boundary); `runner.py` is the
                   trusted in-container script and gets baked into the image
                   at build time — see Dockerfile below
packages/tools/    pure, deterministic data operations only, no LLM calls:
                   excel/csv parsing, profiler heuristics, PII scan (Presidio),
                   chart-spec, join, versioning tools (later phases)
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
  `--reload`'s file-watcher has shown Windows-specific flakiness in this repo
  (two files saved close together can trigger only one reload, silently
  leaving one module stale) - if a code change doesn't seem to take effect,
  don't assume the fix is wrong: kill the process (check both the reloader
  PID and its child worker PID via `netstat -ano` / `tasklist`, both can end
  up holding the port) and do a clean `uv run uvicorn ...` restart before
  concluding anything.
- If `uv` isn't on PATH in a given shell, fall back to `python -m uv <args>` —
  do not reinstall or shadow it another way.
- This machine sits behind a TLS-intercepting corporate proxy (Zscaler) that
  breaks certificate validation for some external hosts — notably GitHub
  Releases (spaCy model wheels, etc.), though not PyPI itself. This is fixed
  via `system-certs = true` under `[tool.uv]` in the root `pyproject.toml` —
  checked into the repo, so it applies no matter which terminal/shell runs
  `uv`. (An earlier attempt set `UV_SYSTEM_CERTS=1` as a permanent user
  environment variable instead; that turned out unreliable in practice —
  freshly opened terminals repeatedly failed to inherit it — which is why
  it moved into project config. If a TLS/certificate error still shows up,
  check that `system-certs = true` is actually present in the root
  `pyproject.toml` before assuming anything else is wrong — never disable
  TLS verification instead.)
- A spaCy model (`en_core_web_sm`, used by Presidio for PII detection) is
  declared as a direct-URL dependency in `packages/tools/pyproject.toml`.
  Don't `python -m spacy download` it ad hoc — that install doesn't survive
  `uv sync`, since anything not declared in a `pyproject.toml` gets removed.

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

## Sandbox execution boundary (non-negotiable)

- Any LLM-generated code that touches a dataset executes only inside the
  sandbox executor in `packages/sandbox` — never via a bare `exec()`/`eval()`
  in the API or worker process.
- The sandbox has no network access, resource/time limits, and a mounted
  dataset copy that is read-only unless the operation is an explicit,
  user-confirmed transform.
- Original uploaded files and every dataset version are immutable once
  written. A transform always produces a new version; nothing is patched
  in place. Undo = repointing the "active version" pointer, never a delete.
- Real security boundary is the container (network isolation, read-only root
  FS, memory/CPU/pids limits) — verified directly in Phase 2 testing (writes
  to `/etc`, `/app`, and even the read-only `/data` mount all fail with real
  `Read-only file system`/`Permission denied` OS errors; only `/scratch` is
  writable). `sandbox.blocklist` is a static AST pre-check on top of that,
  not a substitute for it — don't rely on it alone for a new attack surface;
  reason about what the container itself would do first.
- The sandbox image is a separate build artifact, not part of `uv sync`:
  rebuild it with `docker build -t dataagent-sandbox:latest packages/sandbox`
  whenever `packages/sandbox/Dockerfile` or `packages/sandbox/src/sandbox/runner.py`
  changes — `executor.py` does not auto-rebuild the image, so a stale image
  silently keeps running the old `runner.py`.
- Every execution is a brand-new ephemeral container (no warm pool) - cold
  start is ~0.9-1.0s per run on this machine (measured baseline, Phase 2).
  Revisit only if this becomes an actual bottleneck; don't add pooling ahead
  of that.

## Data handling

- Raw row-level data is never sent to the LLM. Only schema, column
  stats/samples, and aggregated results go into prompts.
- Columns flagged as PII during profiling are excluded from prompts and
  from any response text unless the user explicitly requests otherwise
  post-Phase-7 guardrails.
- Destructive operations (column drop, in-place-feeling overwrite, a join
  that changes row count) require an explicit preview + confirmation step
  once that flow exists — never auto-commit a transform.

## Q&A agent — known scope boundary (as of Phase 3)

`chat_sessions`/`chat_messages` in Postgres persist history, but the agent
itself is stateless per question - `agent.graphs.qa.ask()` takes only
`dataset_id` and `question`, with no prior turns threaded into the prompt. A
follow-up like "what about the median?" can appear to work because the schema
alone makes the right column obvious, not because the agent actually
remembers the previous turn - don't mistake that for working conversational
memory. Real multi-turn context is Phase 6's job (session memory). Don't
partially bolt on conversation history before then; do it properly, in one
phase, with the memory design already scoped for it.

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
