# CLAUDE.md

Rules for this repo. Follow these before applying general defaults — they encode
decisions already made for this project, not preferences to re-derive.

## What this is

A POC AI agent that lets a user upload structured data (CSV/XLSX) and query,
transform, and visualize it through natural language, executing agent-written
Python in an isolated sandbox rather than translating everything to SQL.

## Repo layout (uv workspace)

```
apps/api/        FastAPI backend (package name: api). `services/chat.py` +
                   `routes/chat.py` own the Q&A/propose flow; `services/transforms.py`
                   + `routes/transforms.py` own confirm/reject/undo/export for
                   versioned data (Phase 5) — kept as a separate service/route
                   pair from chat rather than folded in, since none of it goes
                   through the agent graph.
apps/worker/      async job runner (added later)
packages/core/     Settings, DB/S3/Redis client factories, and the catalog
                   ORM models (Dataset/DatasetVersion/Column/ChatSession/
                   ChatMessage/PendingTransform) — the ONLY place connection
                   details are read from
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
                   `_effective_route`. For a "chart" route, the LLM first
                   fills in a small spec (chart_type/x/y/agg/filters,
                   `tools.chart_spec`) - chart_type can name ANYTHING (the
                   prompt allows "radar", "heatmap", etc., not just the
                   structurally-supported set). If chart_type normalizes to
                   one of `tools.chart_spec.SUPPORTED_CHART_TYPES`
                   (histogram/bar/pie/donut/scatter/line/area/box/violin), it
                   is validated/auto-corrected against real column types (not
                   cardinality alone - an id or continuous column is wrong by
                   type regardless of distinct-value count) and
                   `tools.chart_render` deterministically generates the
                   actual plotly code - the LLM never writes plotting code
                   for these. Otherwise it falls back to
                   `_node_generate_freehand_chart`, which DOES let the LLM
                   write plotting code directly (there's no hand-written
                   validator for every conceivable chart type, and trying to
                   build one for each new ask doesn't scale) - but it reuses
                   the same execute→verify→retry loop (so a first-attempt bug,
                   e.g. forgetting to register a dataframe with duckdb before
                   querying it, gets a real chance to self-correct) and its
                   prompt requires the response to explicitly state any
                   assumption made about an ambiguous request rather than
                   silently substituting something. `_effective_route` tags
                   these responses `"chart_freehand"` (never bare `"chart"`)
                   so it's always auditable which charts went through real
                   validation versus best-effort generation - preserve that
                   distinction in any future change here, don't collapse it.
                   When a genuinely new chart type turns out to be requested
                   often (check via the `chart_freehand`-tagged rows in
                   `chat_messages`), that's the signal to promote it into
                   `chart_spec.py`/`chart_render.py`, not to guess upfront.
                   Phase 5 added three more routes to the same plan/execute/
                   verify/respond graph: `"eda"` (deterministic, no LLM call -
                   `tools.eda_render.build_eda_code`), `"transform"` (LLM
                   writes pandas against a `.copy()`, saves
                   `result.parquet`), and `"join"` (LLM only fills a small
                   spec — right-hand dataset name + keys + how — which
                   `tools.join_spec`/`tools.join_render` validate and render
                   into merge code, same "structured spec, deterministic
                   render" pattern as charts). `transform`/`join` never
                   commit anything: on a successful verify they run
                   `_node_finalize_transform`, which diffs the actual
                   before/after schema (`tools.diff.compute_diff` — never the
                   LLM's own account of what it changed, that's unreliable)
                   and uploads the result parquet to a `pending/` S3 key, and
                   `ask()` returns that diff + key for the API layer to turn
                   into a `PendingTransform` row. `_should_retry` is
                   three-way (`retry`/`finalize`/`respond`) specifically so a
                   transform/join only reaches finalize after passing the
                   same verify gate as every other route. `join` additionally
                   resolves the right-hand dataset by fuzzy name match
                   (`schema_context.find_dataset_by_name`) before validating,
                   and downloads it as a second sandbox-mounted file
                   alongside the left dataset — `_node_execute` mounts
                   `right_local_path` under `right_dataset_name` whenever
                   both are present.
packages/sandbox/  isolated code-execution engine. `executor.py` runs
                   arbitrary Python in an ephemeral, hardened Docker
                   container (no network, read-only root FS, memory/CPU/pids
                   limits); `blocklist.py` is a static AST pre-check (defense
                   in depth, not the real boundary); `runner.py` is the
                   trusted in-container script and gets baked into the image
                   at build time — see Dockerfile below
packages/tools/    pure, deterministic data operations only, no LLM calls:
                   excel/csv parsing, profiler heuristics, PII scan (Presidio),
                   chart_spec.py (validates/auto-corrects a chart request
                   against real column types), chart_render.py (turns a
                   validated spec into actual plotly code - embed every
                   value via `repr()`, NEVER `json.dumps()`: `json.dumps(None)`
                   is `"null"`, not valid Python, and silently produces a
                   NameError at execution time, not a caught error), join/
                   versioning tools (later phases). Agent may depend on
                   tools (one-directional); tools must never import agent.
                   A ChartSpec's `date_part` (year/month/quarter,
                   DATE_PART_CHART_TYPES = bar/pie/donut/line/area only)
                   buckets a datetime x before grouping - added specifically
                   because "count per year"-type questions were grouping by
                   the exact raw date instead. Two related things to
                   remember if you touch this code: (1) chart_render's
                   bar/pie "Other" bucket ALWAYS casts the grouped x column
                   to `str` before appending the literal string `"Other"` -
                   without that, a datetime (or a date_part="year" int)
                   column mixed with that one string value makes Plotly
                   misdetect the axis type client-side, so the chart writes
                   successfully (no Python error) but renders blank in the
                   browser - this class of bug is real and already happened
                   once, don't reintroduce it in a new render path. (2) the
                   bar/pie cardinality warning in chart_spec.py is
                   suppressed when date_part is set, since it's computed
                   from the *raw* column's distinct_count, not the
                   post-bucketing count - bucketing usually resolves the
                   cardinality problem the warning would otherwise
                   (wrongly) describe.
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
  (`PendingTransform` — see "Transforms, joins & versioning" below) — never
  auto-commit a transform.

## Transforms, joins & versioning (Phase 5)

- Two-phase propose/confirm, never a direct write. `POST /chat/ask` routing
  to `transform`/`join` only ever produces a `PendingTransform` row (status
  `pending`) plus a parquet sitting under a `datasets/{id}/pending/{uuid}/`
  S3 key — the active dataset and every existing `DatasetVersion` are
  untouched at this point, and nothing else in the catalog can see the
  pending data (it's not linked from any version yet). `POST
  /transforms/{id}/confirm` is the only thing that turns it into a real,
  immutable `DatasetVersion`: it re-profiles the already-produced parquet
  (`tools.profiler.profile_dataframe` + `tools.pii.scan_column`, the same
  path ingestion uses for version 1), copies it to its permanent
  `datasets/{id}/v{n}/data.parquet` key, and repoints
  `dataset.active_version_id`. `POST /transforms/{id}/reject` just deletes
  the pending S3 object and marks the row `rejected` — nothing to undo
  because nothing was ever committed. Both confirm and reject 409 if the
  `PendingTransform` isn't still `pending` (can't resolve twice).
- Undo, redo, and "view an older version" are all the same operation: `POST
  /datasets/{id}/active-version` repoints `active_version_id` to any
  existing version of that dataset. Nothing is deleted or recomputed to do
  this — every version's parquet is already immutable and already exists —
  which is what makes it cheap. `agent.graphs.qa.ask()` re-resolves the
  active version from the catalog on every call (`get_active_dataset`
  → `download_active_version`), so a repoint is immediately visible to the
  next chat question with no separate cache/session state to invalidate.
- The diff shown in a preview (and stored as `DatasetVersion.diff_summary`
  once confirmed) is always computed from the real before/after schema —
  `tools.diff.compute_diff(old_columns, new_columns, old_row_count,
  new_row_count)` reads the actual columns back from the produced parquet
  and the catalog's row count for the base version. Never ask the LLM to
  self-report what it changed for this — it can miscount or misdescribe,
  and the whole point of a preview is that it's trustworthy.
- Export (`GET /datasets/{id}/export?format=csv|xlsx|parquet&version_id=...`)
  works against any historical version, not just the active one —
  `version_id` is optional and only defaults to `active_version_id` when
  omitted. It downloads that version's already-immutable parquet directly
  from S3 and converts in-memory; it never re-runs any transform code.
- EDA (`tools.eda_render.build_eda_code`) is deliberately NOT
  ydata-profiling: that package hard-pins `pandas<3.0`
  (`ydata-profiling==4.14.0` → `pandas!=1.4.0,<3.0,>1.1`), which conflicts
  with this project's `pandas==3.0.6` pin in `packages/sandbox/Dockerfile`
  (confirmed via a real `docker build` failure, not assumed) — forking the
  sandbox image onto a different pandas version for one feature risks
  subtle behavioral differences everywhere else that already assumes
  pandas 3.0.6 semantics (Q&A, charts, transforms). The custom report uses
  only pandas + plotly (already in the sandbox image), and caps both
  profiled and charted columns (`_MAX_PROFILE_COLUMNS` /
  `_MAX_CHART_COLUMNS` in `eda_render.py`) so a wide dataset still produces
  a report in bounded time/size.

## Q&A agent — known scope boundary (as of Phase 3)

`chat_sessions`/`chat_messages` in Postgres persist history, but the agent
itself is stateless per question - `agent.graphs.qa.ask()` takes only
`dataset_id` and `question`, with no prior turns threaded into the prompt. A
follow-up like "what about the median?" can appear to work because the schema
alone makes the right column obvious, not because the agent actually
remembers the previous turn - don't mistake that for working conversational
memory. Real multi-turn context is Phase 6's job (session memory). Don't
partially bolt on conversation history before then; do it properly, in one
phase, with the memory design already scoped for it. This applies equally to
transform/join (Phase 5) — "confirm this" is handled via the explicit
`PendingTransform` id, not by the agent recalling what it just proposed.

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
