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
apps/web/         Next.js (App Router) + React + TypeScript frontend - the
                   only non-Python, non-uv part of this repo (own
                   package.json, `npm install`/`npm run dev`/`npm run build`
                   from apps/web, not uv). Rewritten from a lightweight Vite
                   SPA to this Next.js app after all core backend phases
                   landed - see "Frontend rewrite" below for the full
                   rationale and architecture; the "deliberately excluded
                   from the uv workspace" reasoning is unchanged by the
                   rewrite (`exclude = ["apps/web"]` in the root
                   pyproject.toml, since it has no pyproject.toml and the
                   workspace glob would otherwise error). `next.config.ts`'s
                   `rewrites()` proxies `/api/*` to `http://localhost:8000`
                   exactly like the old `vite.config.ts` dev proxy did, so
                   the frontend never needs CORS config or a hardcoded
                   backend origin - `src/lib/api/client.ts` always calls
                   relative `/api/...` paths. Talks only to the existing
                   REST endpoints (chat, transforms, datasets, users) - no
                   new backend surface beyond the one small additive
                   `GET /users/me` (see "Frontend rewrite" below) - and it
                   has no build step or dependency on anything under
                   `packages/`.
apps/worker/      async job runner (added later)
packages/core/     Settings, DB/S3/Redis/Chroma client factories, and the
                   catalog ORM models (Dataset/DatasetVersion/Column/User/
                   ChatSession/ChatMessage/PendingTransform) — the ONLY place
                   connection details are read from
packages/agent/    LangGraph graphs, the OpenRouter LLM wrapper, and any
                   other LLM-calling logic. `memory.py` is Phase 6's
                   short-term (Redis)/long-term (Chroma) memory — see
                   "Memory & personalization" below, this is its own section
                   because it's cross-cutting (every route's prompt now
                   includes a memory_context block) rather than one more
                   route like Phase 5's. `schema_context.py` renders
                   catalog metadata (names/types/descriptions/distinct
                   counts/bounded sample values) into prompt text — this is
                   the ONLY dataset-derived content that ever reaches an LLM
                   prompt; PII-flagged columns get a warning instead of
                   sample values. `graphs/orchestrator.py` is the default
                   Q&A entry point as of Phase 7 (tool-calling, not a fixed
                   router) — see "Q&A agent architecture" below for why and
                   how. `graphs/qa.py` is kept as both the documented
                   fallback graph AND the library orchestrator.py's tools
                   are built on (`run_route`) — its own internal routing is
                   still exactly as described below, since that's what both
                   graphs actually run underneath. `graphs/qa.py` is the
                   read-only Q&A agent: plan (routes sql/pandas/clarify) →
                   generate_code → execute
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
                   (wrongly) describe. (3) every `fig.write_html`/`to_html`
                   call - in chart_render.py, eda_render.py, and the
                   freehand chart prompt in qa.py - MUST use
                   `include_plotlyjs=True`, never `"cdn"`. This isn't a size
                   preference: the sandbox's installed `plotly` Python
                   package has a broken internal version constant
                   (`plotly.offline.get_plotlyjs_version()` hardcodes
                   `"4.1.1"`) that `"cdn"` mode trusts to build the CDN URL,
                   which fetches a real but genuinely old plotly.js v4.1.1
                   from cdn.plot.ly that can't parse the compact
                   `bdata`/`dtype` typed-array encoding this plotly version's
                   JSON output uses - the chart writes with no Python error
                   and renders completely blank in the browser. Confirmed via
                   direct inspection: the plotly.min.js actually bundled with
                   the installed package (used by `True`/inline mode) DOES
                   handle `bdata`, despite its own header comment also
                   claiming "v4.1.1" - only the CDN-fetched copy is really
                   stale. If `packages/sandbox/Dockerfile`'s plotly version
                   ever changes, re-verify this before assuming "cdn" is
                   safe again - don't just trust the version string.
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

- Postgres, MinIO (S3-compatible), Redis, Chroma, and Phoenix (Phase 7
  observability - see its own section below) run via `docker-compose up` —
  never installed natively, never SQLite, never a bare filesystem folder
  standing in for object storage. The "never SQLite" rule is about this
  project's own data stores - Phoenix's internal SQLite (inside its own
  container, not something this codebase talks to directly) is a
  vendored third-party tool's storage choice, not an exception being made
  for this project's data.
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

## LLM provider — OpenRouter, Gemini, or Vertex AI via LangGraph, not the Claude Agent SDK

- All model calls go through `agent.llm.get_chat_model()` — the only place a
  chat model is instantiated anywhere in this project. It branches on
  `LLM_PROVIDER` (`.env`, default `openrouter`):
  - `openrouter`: wraps `langchain_openai.ChatOpenAI` pointed at OpenRouter
    (`OPENROUTER_BASE_URL`/`OPENROUTER_MODEL`/`OPENROUTER_API_KEY`). Note:
    OpenRouter's free models genuinely get retired/rate-limited over time
    (observed live twice — one model returned 404 "no longer available",
    another 429 "temporarily rate-limited upstream") — this is normal
    operation of shared free infrastructure, not a bug, when it happens
    check https://openrouter.ai/models?max_price=0 for a current one.
  - `gemini`: wraps `langchain_google_genai.ChatGoogleGenerativeAI` with
    `vertexai` unset, calling Google AI Studio directly
    (`GEMINI_MODEL`/`GEMINI_API_KEY`, simple API-key auth) — added so
    development can use AI Studio's free tier instead of paying OpenRouter
    for a Gemini model it already proxies.
  - `vertex`: wraps the **same** `ChatGoogleGenerativeAI` class with
    `vertexai=True` plus `VERTEX_PROJECT`/`VERTEX_LOCATION` -  Google's
    unified `google-genai` SDK (which `langchain-google-genai` 4.0+ is
    built on) speaks to both the AI Studio and Vertex AI backends through
    one client, so this does NOT use the separate `langchain-google-vertexai`
    package deliberately: that package's dependency chain caps
    `pyarrow<24.0.0` across every published version, which conflicts
    outright with this project's `pyarrow==25.0.1` pin (kept in sync with
    the sandbox image - see that pin's own comment) - confirmed via a real
    `uv add` resolution failure, not assumed. Vertex AI is a paid,
    separately-billed GCP product from AI Studio even though it serves the
    same model family, and its auth is different too - not a simple API
    key by default. `get_chat_model()` supports three auth paths for it,
    tried in this order: `VERTEX_API_KEY` (a Vertex-AI-enabled key, **not**
    interchangeable with `GEMINI_API_KEY`) if set; else
    `VERTEX_CREDENTIALS_PATH` (a downloaded service-account JSON key) if
    set; else falls through to Application Default Credentials (`gcloud
    auth application-default login`, or the environment's own service
    identity when deployed). `VERTEX_PROJECT` must be the project ID
    (the short string), never the numeric project number - the client
    rejects the number.
  - Any other `LLM_PROVIDER` value raises `ValueError` at call time rather
    than silently falling back to a provider — fail fast on a typo instead
    of quietly billing/calling the wrong service.
  - Swapping providers (or swapping models within one provider) is always a
    one-line env change. Never instantiate a chat model directly anywhere
    else, and never branch on `LLM_PROVIDER` outside `llm.py` — any new
    provider-specific logic belongs inside `get_chat_model()`, not at a
    call site.
- Do not introduce the Claude Agent SDK or the Anthropic SDK into this
  project — it does not support routing to arbitrary OpenRouter/Gemini/
  Vertex models and is not the chosen harness here. Agent orchestration is
  LangGraph only.
- API keys/credentials (`OPENROUTER_API_KEY`, `GEMINI_API_KEY`,
  `VERTEX_API_KEY`, `VERTEX_CREDENTIALS_PATH`) are read from `.env` via
  `core.settings`, never hardcoded, never logged, never included in a prompt
  or committed to a file. A service-account JSON key file (when
  `VERTEX_CREDENTIALS_PATH` is used) lives outside the repo entirely, same
  rule as any other credential.

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
  from any response text — see "Guardrails: PII enforcement & RBAC" below
  for how this is actually enforced, not just prompted for.
- Destructive operations (column drop, in-place-feeling overwrite, a join
  that changes row count) require an explicit preview + confirmation step
  (`PendingTransform` — see "Transforms, joins & versioning" below) — never
  auto-commit a transform.

## Guardrails: PII enforcement & RBAC (Phase 7)

- **PII enforcement is a redaction chokepoint, not a prompt hint.**
  `schema_context.build_schema_context()`'s `[PII - do not echo raw values]`
  warning (see "Data handling" above) was always just a request the code-gen
  LLM could ignore or get wrong - it never actually stopped a raw PII value
  from reaching a prompt or a user-facing answer if generated code selected
  that column anyway. The real enforcement is
  `tools.pii.redact_pii_from_result(result, pii_columns, pii_values)`,
  called from `agent.graphs.qa._node_execute` on `res.result` before it's
  stored into `state["execution_result"]["result"]` - the single node both
  `build_qa_graph()` and `run_route()` (what every orchestrator tool calls)
  share, so this applies uniformly to every route (Q&A, chart, EDA,
  transform, join) with no route-specific duplication. `pii_columns` comes
  from `schema_context.get_pii_column_names(dataset_id)`, combined with the
  right-hand dataset's PII columns too when the state has one (a join).
  This is also what closes the short-term-memory leak path:
  `agent.memory.append_turn` stores the `answer` text verbatim, and
  `answer` is built from this already-redacted result, so a PII value
  redacted on turn N can't resurface by being replayed into turn N+1's
  prompt via `render_recent_turns`.
  Two independent checks, not one - verified live that the first alone
  wasn't enough:
  - **By column name**: a plain dict/list-of-dicts (a `.to_dict()` result)
    or the sandbox's dataframe-preview shape
    (`{"type": "dataframe", "columns": [...], "preview": [...]}`, whose
    `columns` list is filtered too, not just each preview row, so a caller
    can't tell the column was even selected).
  - **By value**: `pii_values` - the PII columns' actual values, read
    host-side (never inside the sandbox) straight from the local dataset
    parquet by `qa._collect_pii_values`, capped at 5000 values per column
    so a high-cardinality PII column can't make the scan itself expensive.
    This exists because the name-based check has a real blind spot: code
    like `df["region"].unique().tolist()` returns a bare list of strings
    with no column name anywhere in the structure - reproduced live before
    this existed (a PII-flagged "region" column's raw values reached the
    final answer untouched even though `redact_pii_from_result` was
    already wired in), fixed by adding the value-level check, then
    re-verified by grepping the raw exported Phoenix trace content for
    those exact values and confirming they're genuinely absent - not just
    checking the API response looked right.
  Known gap: a PII column's raw *values* used as dict KEYS (e.g. a
  `groupby("email")` result -> `{"a@x.com": 5}`) aren't caught - redacting
  a key would either leave the value in place as the key anyway, or
  collapse multiple distinct keys onto one placeholder and silently lose
  data, so this stays a documented gap rather than a half-fix.
- **RBAC role lives on the `User` row, never on the request.**
  `core.models.User.role` (`"viewer"` | `"editor"`, default `"viewer"`) is
  only ever changed by a direct DB update - `scripts/promote_user.py
  <external_id> <viewer|editor>` - or a future admin endpoint. No API
  request body or query param can set a caller's own role: `POST
  /chat/ask`'s `user_id` and everywhere else a caller supplies an identity
  only ever *resolve* an existing user (`api.services.users.
  get_or_create_user`, which never sets `role`), never grant one. This is
  what makes the checklist's "even by asking the agent directly"
  requirement true architecturally, not just at one endpoint: there is no
  path, prompt-injection or otherwise, that lets a request change its own
  privilege.
- **Enforcement point is the commit, not the propose.** `POST
  /transforms/{id}/confirm` and `POST /datasets/{id}/active-version` both
  require `role == "editor"` (`api.services.users.require_editor`, checked
  via a `user_id` query param, 403 on an under-privileged or unknown user -
  an unknown external_id fails exactly like a viewer, never a 404, so a
  probe can't use this to learn who has an account). `POST
  /transforms/{id}/reject` is deliberately left open to any role - it only
  discards a preview, never mutates committed state. This lines up with
  the existing two-phase propose/confirm boundary (see "Transforms, joins &
  versioning" below): the orchestrator's `propose_transform`/
  `propose_join` tools already only ever produced a `PendingTransform`
  preview and had no code path to the confirm endpoint at all - Phase 7
  doesn't change that shape, it gates the one place a human (or an agent
  impersonating one) could otherwise commit. Verified live: a viewer-role
  user asking the agent directly to "drop this column and commit it
  without asking me to confirm" still only gets offered a preview
  (`route: "chat"`, no tool call at all in that case); a viewer hitting
  `confirm` against a real, agent-proposed `PendingTransform` id gets 403
  and the transform stays `pending`; the same id confirmed by an editor
  succeeds and produces a new `DatasetVersion`.
- No login exists in this POC (see "Frontend identity" below), so `role`
  is the only access boundary that exists right now - treat a "viewer" as
  the default, least-privilege assumption for any new mutating endpoint,
  the same way `User.role` already defaults to it.

## Observability (Phase 7) - Arize Phoenix, not Langfuse

- **Phoenix, not Langfuse, and not by default preference.** Langfuse was
  the originally-requested tool; ruled out after directly measuring this
  machine (15.7GB total RAM): Langfuse v3 needs Postgres+ClickHouse+Redis+
  MinIO plus a stated 16GB minimum for itself alone, and v2 (Postgres-only)
  has been in maintenance mode with security updates ended Q1 2025 -
  effectively EOL. Comet Opik was also evaluated and ruled out as
  architecturally just as heavy (ClickHouse+ZooKeeper+MySQL+Redis+MinIO,
  ~10 containers). Phoenix runs as a **single** SQLite-backed container
  (`arizephoenix/phoenix:latest` in `docker-compose.yml`, ports 6006 =
  UI/dashboard + OTLP HTTP, 4317 = OTLP gRPC), is OpenTelemetry-native (not
  a proprietary SDK - portable instrumentation), and has built-in cost
  tracking. If you're ever tempted to swap in Langfuse because that's the
  "standard" choice, re-measure this machine's free RAM first - the
  constraint that ruled it out hasn't changed just because it's a common
  recommendation elsewhere.
- **Registration is one call, one place.** `agent.tracing.setup_tracing()`
  is called once, from `apps.api.main`'s lifespan, before any graph runs -
  never instantiate a tracer/instrumentor anywhere else. It's a no-op when
  `PHOENIX_ENABLED=false` (the default - so a Phoenix-less dev setup, e.g.
  a quick unit test run, doesn't fail trying to export spans nowhere).
  When enabled, it calls `phoenix.otel.register()` then
  `openinference.instrumentation.langchain.LangChainInstrumentor().
  instrument(...)` - this one call auto-instruments every `model.invoke()`
  (every route's LLM call, since they all go through `agent.llm.
  get_chat_model()`) AND every LangGraph node execution as its own child
  span, because a compiled `StateGraph` is itself a LangChain `Runnable`
  traced through the same callback manager - verified live, "plan"/
  "load_dataset"/"verify"/"respond"/etc. all show up as spans with zero
  manual instrumentation.
- **The one thing that needs manual instrumentation: sandbox execution.**
  `sandbox.executor.run_python` is a raw subprocess call, not a LangChain/
  LangGraph construct, so `qa._node_execute` wraps it in an explicit
  `sandbox.execute` span (`agent.tracing.get_tracer()`, attributes: route,
  attempt, timeout, success/timed_out/oom_killed/rejected) - without this
  the execution phase would be an opaque gap in an otherwise fully
  annotated trace. `get_tracer()` returns `None` when Phoenix is disabled;
  every call site wraps the manual span in `contextlib.nullcontext()` in
  that case so it's never load-bearing for a request to work.
- **Orphaned-trace fix - real bug, found and fixed live, not theoretical.**
  A manually-created span (`tracer.start_as_current_span(...)`) relies on
  ambient OTel context propagation. LangChain/LangGraph's own spans don't -
  OpenInference nests them via LangChain's callback run-tree instead, which
  is independent of Python's ambient context. The result, confirmed by
  directly inspecting exported spans via Phoenix's REST API: the manual
  `sandbox.execute` span came out with a **completely different trace_id**
  from the rest of the question's trace - not just mis-nested, a genuinely
  separate, orphaned trace, even though it's plain synchronous Python code
  called moments after a correctly-nested LLM span in the exact same call
  stack. Fixed by wrapping the entire `graph.invoke(...)` call in both
  `orchestrator.ask()` and `qa.ask()` in its own explicit span
  (`"orchestrator.ask"` / `"qa.ask"`) - this gives the plain-Python code
  underneath (the `run_route()` loop, `_node_execute`) a real ambient
  parent to attach to. Verified live after the fix: one trace_id, one
  question, `sandbox.execute` a direct child of the wrapping span (a
  sibling of LangGraph's own auto-instrumented tree, not nested inside
  `run_tools` specifically - reaching into LangChain's internal run-tree to
  nest it more precisely wasn't pursued, since "same trace, not orphaned"
  is what the checklist actually requires). If you add another manual span
  anywhere in the agent, wrap it the same way or verify empirically (via
  the REST API, not the UI - the UI can visually hide a missing-parent
  span) that it lands in the right trace.
- **Large payloads must never sit in LangGraph state - a second, bigger
  trace-completeness bug, found the same way (empirically, not assumed
  fixed after the orphan fix above).** After the orphan fix, chart/EDA
  requests were STILL intermittently missing spans - including, confusingly,
  spans belonging to OTHER, unrelated requests made around the same time.
  Root cause, found by reading the actual uvicorn server log rather than
  just re-inspecting Phoenix: `opentelemetry.exporter.otlp.proto.grpc.
  exporter` was logging `RESOURCE_EXHAUSTED - Received message larger than
  max (4982113 vs. 4194304)`, retrying, and then failing outright with
  `DEADLINE_EXCEEDED` once the retried batch grew to ~40MB. Chart/EDA
  artifacts are inline-plotly HTML - several MB each, per the sandbox's
  hard `include_plotlyjs=True` requirement (see packages/sandbox notes
  above) - and `orchestrator.py`'s `OrchestratorState` used to carry the
  full decoded HTML string in an `artifact_html` field. Because
  LangChainInstrumentor auto-traces every LangGraph node's full state as
  span input/output, that multi-MB string got re-serialized into EVERY
  subsequent node's span for the rest of that graph run (the follow-up
  LLM call, `save_turn`, `finalize`, ...) - confirmed directly:
  `artifact_html`'s actual length (`4825144` bytes) matched the exporter's
  logged oversized-message size almost exactly. Once one export batch
  exceeded Phoenix's OTLP gRPC receive limit (4MB, not currently
  configurable from this side), the WHOLE BATCH failed - which is why
  unrelated requests batched alongside it also lost spans, not just the
  chart trace itself. Fixed at the source, not by raising a limit: the
  state field was renamed to `artifact_ref` (a short uuid4 string) and the
  real bytes moved to `orchestrator._artifact_sidecar`, a plain
  process-local dict outside any traced state - `ask()` resolves the ref
  back to real content and pops it immediately, after `graph.invoke()`
  returns, so it's never part of anything OpenInference serializes.
  **The same bug existed a second, worse time**: `qa.py`'s own
  `build_qa_graph()` is a real compiled `StateGraph` too (unlike
  `run_route()`'s plain-Python replay loop), so `QAState`'s old
  `artifacts: dict[str, bytes]` field - the RAW bytes, not even
  decoded-to-string - re-serialized into every node from `execute` through
  `respond`/`save_turn`. Verified live via `use_legacy_qa_graph: true`: a
  chart request there hit `RESOURCE_EXHAUSTED` at ~44.7MB, worse than the
  orchestrator path's ~40MB, for the same underlying reason. Fixed the same
  way: `QAState.artifacts` replaced with `artifact_ref` (a sidecar key) +
  `artifact_keys` (just the key names, e.g. `["chart.html"]` - cheap, and
  all `_node_verify`'s presence checks actually need); the real bytes live
  in `qa._artifact_sidecar`, read (not popped - `run_route()` callers still
  need it afterward) by `_node_finalize_transform` for `result.parquet`,
  and popped exactly once at the end by whichever caller resolves it last -
  `qa.ask()` for the legacy graph, `orchestrator._extract_tool_result` for
  the orchestrator path (which imports `qa._artifact_sidecar` directly,
  matching this codebase's existing pattern of orchestrator.py sharing
  qa.py's private node functions rather than reimplementing them).
  Verified live: re-ran the exact chart/EDA requests that previously
  triggered `RESOURCE_EXHAUSTED` (including back-to-back with three other
  questions to reproduce the batching conditions, and via both the
  orchestrator AND `use_legacy_qa_graph: true`) - zero export errors, every
  trace's root span present, and confirmed a transform still completes
  correctly end-to-end (`_node_finalize_transform`'s sidecar read path).
  **The general rule this leaves behind**: never add a field to
  `QAState`/`OrchestratorState` that can hold more than a few KB (an
  artifact, a large raw result, anything file-sized) - route it through a
  side channel the same way, in BOTH graphs if the field is shared between
  them, or a future large payload will silently re-trigger this same
  failure mode, including for requests that have nothing to do with it.
- **A trace's root span isn't reliably exported without an explicit
  flush.** Even after both fixes above, force-flushing matters:
  `agent.tracing.flush()` (calls the global tracer provider's
  `force_flush()`) is called once per request from `apps.api.services.
  chat.ask_question`, right after `agent_fn()` returns, for every request
  path - not just the happy path. Without it, `BatchSpanProcessor`'s
  default ~5s export interval left a real, observed window where a
  request's root span (which ends last, since it wraps the whole
  `graph.invoke()` call) hadn't been flushed yet when the next request's
  spans started queuing - not a guaranteed loss on its own, but removing
  the dependency on batch timing entirely is what made trace completeness
  actually reliable under back-to-back requests instead of "usually fine."
- **PII verification is done by grepping raw trace content, not trusting
  the code** - the checklist's own explicit requirement. Phoenix's REST API
  (`GET /v1/projects/{id}/spans`) returns full prompt/completion text and
  tool-call arguments per span (confirmed by inspecting real captured
  content: generated code, LLM outputs, tool arguments all present in
  full) - after the PII redaction fix above, a live question that directly
  asked for a PII-flagged column's raw values was re-run, and the raw
  values were confirmed **absent** from the entire exported trace by
  string-searching the actual span dump, not just checking that the API's
  JSON response looked redacted.
- **Cost tracking needs per-model configs - there's no default for
  OpenRouter/Gemini/Vertex models.** Phoenix ships ~63 default pricing
  configs for OpenAI/Anthropic model names; none of this project's models
  (`openrouter_model`, `gemini_model`, `vertex_model` - see "LLM provider"
  above) match those, so cost stays $0 until configured. Done via Phoenix's
  GraphQL `createModel` mutation (`name`, `provider`, `namePattern` matched
  against the span's `llm.model_name`, `costs: [{tokenType: "input"|
  "output", kind: PROMPT|COMPLETION, costPerMillionTokens}]`) - equivalent
  to Settings > Models in the Phoenix UI at `localhost:6006`, done here via
  the API instead so it's reproducible. All three currently-configured
  models have a cost config as of this writing; if `VERTEX_MODEL`/
  `GEMINI_MODEL`/`OPENROUTER_MODEL` ever change, add a matching one via the
  same mutation (or the UI) or that model's spend silently reads as $0.
  Verified live via GraphQL `costSummary`/`spanLatencyMsQuantile` on the
  `voiceagent` project that both a real, non-zero $ total and a real
  latency quantile come back - this is also exactly what the Phoenix UI's
  own cost/latency dashboard reads, so "what did this cost us yesterday"
  is answerable there without reading raw logs, per the Phase 7 checklist.
- `PHOENIX_ENABLED`/`PHOENIX_COLLECTOR_ENDPOINT`/`PHOENIX_PROJECT_NAME` are
  read from `.env` via `core.settings`, same as every other connection
  detail in this project - see "Local infra = docker-shaped cloud infra,
  always" above for why nothing about this should ever be hardcoded.

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

## Q&A agent architecture — orchestrator (Phase 7) is the default, qa.py is a kept fallback

`agent.graphs.orchestrator.ask()` — not `agent.graphs.qa.ask()` — is what
`apps/api/services/chat.py` calls by default. This replaced a fixed
router+per-route-subgraph design (`agent.graphs.qa`: an LLM classifies every
question into exactly one of `sql`/`pandas`/`chart`/`eda`/`transform`/`join`/
`clarify`, each with its own dedicated graph nodes) because that shape has
no slot for anything that isn't a fresh data request - a follow-up asking
to interpret a prior result ("what does reject H0 mean"), a short "yes"
confirming something the agent itself just offered, or plain conversation
(greetings, thanks) all got force-fit into `clarify`, which caused a real,
reproduced-live bug: a conversation stuck re-asking a near-identical
clarifying question 3 times in a row even after the user answered "yes"
twice, because nothing in that architecture could route "yes" anywhere
useful either.

`agent.graphs.orchestrator` is a tool-calling design instead: one LLM node
bound to five tools (`query_data`, `make_chart`, `run_eda`,
`propose_transform`, `propose_join`) decides whether a question needs zero,
one, or more of them, looping (capped at `MAX_TOOL_ROUNDS`) until it either
calls a tool or answers directly. Anything that doesn't need fresh
computation against the data - explanation, confirmation, chitchat - gets a
direct answer with no tool call at all (`route` comes back as `"chat"` for
these), which is exactly the class of turn the old router had no home for.

This is not a rewrite of the safety-critical logic, and that's deliberate:
- **Every tool is a thin wrapper around `agent.graphs.qa.run_route(route,
  state)`** - the *exact* node functions (`generate_code`, `execute`,
  `verify`, retry, chart-spec validation with freehand fallback, transform
  diff computation) the original graph already used, replayed for a route
  the orchestrator decided instead of qa.py's own router LLM call.
  `run_route` and `build_qa_graph()` share the same `_NODE_FUNCS`/
  `_EDGE_FUNCS`/named `_route_after_*` dispatch functions - if you change
  routing logic in one, update the other; they're meant to be driven by the
  identical functions, not two implementations that could drift apart.
  Every fix already made to that logic (`ARTIFACTS_DIR`/numpy/duckdb
  sandbox pre-imports, the empty-result verify fix, the chart
  multi-column-request fallback) applies to the orchestrator automatically.
- **`propose_transform`/`propose_join` still only ever produce a preview** -
  they call `run_route("transform"/"join", ...)`, the same deterministic
  propose pipeline as before. Committing is still a separate, human-gated
  `POST /transforms/{id}/confirm` call the orchestrator's tools cannot
  reach - the two-phase safety boundary described under "Transforms, joins
  & versioning" is unchanged by this architecture switch.
- **PII discipline is inherited, not reimplemented** - tool results are the
  same aggregated/formatted `answer` text `_node_respond_answer` already
  produced, never raw execution output, so `schema_context.py` remains the
  only channel of dataset-derived content that reaches any prompt.
- The orchestrator's system prompt (`_ORCHESTRATOR_SYSTEM_PROMPT`) has an
  explicit rule to quote a tool-returned number exactly, never round or
  paraphrase it - the one new failure mode this architecture introduces
  that the old one didn't have is an extra LLM pass to produce the final
  conversational answer, which could otherwise silently reword a precise
  result.

`agent.graphs.qa` is **kept, not deleted**, as a documented fallback: pass
`use_legacy_qa_graph: true` on `POST /chat/ask` to route a specific request
through it instead - an operator/debugging escape hatch (e.g. to check
whether an issue is specific to the orchestrator), not a user-facing
choice. There is deliberately no frontend UI for this switch.

**Phase 7 is not complete just because the orchestrator is the default.**
What exists now is the architecture and a hand-tested prototype - the
guardrails half of "Guardrails, Permissions & Observability" (per-session/
per-user tool-call budgets beyond the flat per-turn `MAX_TOOL_ROUNDS` cap,
durable audit logging of tool calls beyond the terminal, dedicated PII-leak
testing for the orchestrator's accumulating multi-round message history,
permission checks) has not been built yet. Don't treat this as
production-hardened until that work lands and the verification checklist
for it has actually been run, per "Before considering any phase done" below.

## Q&A agent — scope boundary superseded by Phase 6

Phases 1-5 built this stateless: `agent.graphs.qa.ask()` took only
`dataset_id` and `question`, no prior turns threaded into the prompt, and a
follow-up like "what about the median?" only appeared to work because the
schema alone made the right column obvious. Phase 6 replaced this - see
"Memory & personalization" below - `ask()` now takes `user_id`/`session_id`
and genuinely threads short- and long-term context into every prompt. This
applies equally to transform/join (Phase 5) — "confirm this" is still
handled via the explicit `PendingTransform` id, not by the agent recalling
what it just proposed, since memory is about interpreting language, not a
substitute for that already-explicit confirmation mechanism.

## Memory & personalization (Phase 6)

- No auth exists in this POC. `User` is a lightweight identity anchor, not a
  login system: `apps.api.services.users.get_or_create_user(external_id)`
  resolves-or-creates a row keyed on whatever stable string the caller
  supplies as `user_id` in the `/chat/ask` request body (an email, a
  frontend-generated id — anything stable). `ChatSession.user_id` is
  nullable (pre-Phase-6 sessions have none) but every session created from
  here on always sets it, and re-using a `session_id` that belongs to a
  different resolved user is rejected (`ValueError` → 400).
- Short-term memory is per-session, Redis-backed, and deliberately NOT the
  same thing as `chat_messages` in Postgres. Postgres is the permanent
  record regardless of what happens to Redis; Redis
  (`agent.memory.append_turn`/`get_recent_turns`, key
  `session:{id}:turns`, capped at the last 6 turns, 24h TTL) exists
  specifically so a follow-up like "what about EMEA?" can be resolved
  against the actual previous question instead of the agent guessing from
  schema alone. Because it's Redis and not a Python-process dict, session
  context survives an API server restart — verified directly
  (mid-conversation restart, follow-up still resolved correctly, turns
  still present in Redis) per the Phase 6 checklist. Losing Redis (a
  flush, an expired TTL) degrades gracefully to the old stateless behavior
  for that session — it never loses anything that isn't also in Postgres.
- Long-term memory is corrections/preferences a user has stated about a
  specific dataset ("region means the sales team's region, not the
  customer's"), embedded and stored in Chroma
  (`agent.memory.remember_fact`/`retrieve_facts`, collection
  `user_memory`), retrieved by semantic similarity to the current question
  AND filtered by `{user_id, dataset_id}` via Chroma's `where` — never by
  similarity alone. This filtering is load-bearing, not a nice-to-have:
  verified directly that a fact stored for one dataset does not leak into
  a different dataset for the same user, and a fact stored by one user
  does not leak to a different user on the same dataset. Embeddings use
  Chroma's bundled default embedding function (ONNX MiniLM, downloaded
  once to `~/.cache/chroma/onnx_models/` on first use, then fully local) —
  deliberately not OpenRouter or a `sentence-transformers`+torch install:
  OpenRouter's embedding support is inconsistent across models (this
  project pins a specific free chat model, not an embedding one), and
  torch is a multi-hundred-MB install this project has no other reason to
  carry. If that first-use download ever fails on this network, that's the
  same class of issue as the spaCy model download (see the proxy note
  under Package management) — retry, don't disable cert verification.
- The `remember(fact)` capability is genuinely agent-driven, not hardcoded
  pattern matching: every turn runs through `_node_extract_memory` first
  (`_MEMORY_EXTRACT_PROMPT`), which decides whether the message is
  teaching/correcting something reusable versus just asking a normal
  question, and only calls `memory.remember_fact` when it judges the
  former. This costs one extra LLM call per turn (latency/token cost) — a
  deliberate tradeoff for genuinely open-ended detection instead of
  hardcoded trigger phrases like "remember that...". A message can be a
  pure correction (routed straight to a `"memory_ack"` response, skipping
  `plan`/`execute` entirely — there's no real question to answer) or a
  correction bundled with a real question (the fact is still stored, but
  the graph continues through the normal plan→execute→verify flow and the
  acknowledgment is prefixed onto the real answer) — both were verified
  live, not just implemented.
- Every route's prompt (`_ROUTER_PROMPT`, `_CODE_PROMPT`,
  `_CHART_SPEC_PROMPT`, `_FREEHAND_CHART_PROMPT`, `_TRANSFORM_PROMPT`,
  `_JOIN_SPEC_PROMPT`) now has a `{memory_context}` slot
  (`_build_memory_context`) combining retrieved long-term facts and recent
  short-term turns. If you add a new prompt that generates code or a spec
  against the dataset, give it this slot too — omitting it silently
  reintroduces the "agent repeats past mistakes" problem Phase 6 exists to
  fix.

## Multi-dataset chat sessions

A `ChatSession` is no longer pinned to one dataset — `chat_session_datasets`
is a many-to-many join table (`ChatSession.dataset_links`), and `POST
/chat/ask` takes `dataset_ids: list[str]`. Any id not already attached to
the session gets attached (this is what lets a file be added mid-chat, not
just at session start); starting a *new* session with an empty list is
rejected (`ValueError` → 400) since a session has to have at least one
dataset. Which ONE attached dataset a given question is actually about is
resolved inside the agent graph, not the API layer:
`_node_resolve_target_dataset` runs first (trivial passthrough when only one
dataset is attached — no LLM call, zero overhead for the common case; an
LLM call given `schema_context.describe_datasets_brief` when there's more
than one). If it can't confidently pick one, it sets `route="clarify"` and
short-circuits straight to `respond` rather than guessing — verified live
("how many rows are there?" against a session with two attached datasets
correctly asked which one, instead of silently answering for the wrong
one). Only after a single dataset is resolved does `_node_load_dataset` run
(schema, active-version download) — this used to be `ask()`'s preamble
before the graph even started; it moved inside the graph because with
multiple datasets attached, nothing about which dataset to load is known
until resolution has run. `QAResult.resolved_dataset_id` is what the API
layer uses to build `PendingTransform` rows against the right dataset — a
transform/join always targets whichever dataset was resolved for that
turn, never a fixed "primary" one. This doesn't change how `join` finds its
*second* dataset (`find_dataset_by_name` still searches every dataset in
the catalog, not just ones attached to the session) — that's a deliberate
scope boundary, not an oversight; conflating "attached to this session" with
"joinable" would make join's dataset lookup session-dependent for no clear
benefit yet.

## Dataset visibility

`Dataset.uploaded_by_user_id` (nullable FK to `users.id`) scopes `GET
/datasets` to the requesting user's own uploads - `_find_existing` (the
content-hash dedup in `ingestion.py`) is keyed on `(content_hash,
uploaded_by_user_id)`, not content alone, so two different users uploading
byte-identical files each get their own `Dataset` row rather than silently
sharing one they don't own. This is a **listing default, not an
authorization system**: `GET /datasets/{id}` and everything downstream
(chat, transforms, export) still work by id regardless of who uploaded it -
real enforcement (can this user even open this dataset) is Phase 7 scope,
not built yet. Don't read the current filtering as "access control" in a
design doc or to a user - it only controls what shows up in the default
list. A dataset with `uploaded_by_user_id IS NULL` (created before this
column existed) is unlisted for everyone by design, not shown as
"shared"/fallback-visible - if you ever need to backfill ownership for
pre-existing rows again, do it explicitly (`UPDATE datasets SET
uploaded_by_user_id = :uid WHERE uploaded_by_user_id IS NULL`), don't assume
NULL should keep meaning "visible to all."

## Frontend rewrite: Next.js (App Router), replacing the Vite SPA

The original `apps/web` was a deliberately lightweight Vite SPA built to
unblock backend phases - no router, no component library, one prop-drilled
`App.tsx`. Once PII/RBAC/observability (Phase 7) landed, it was rebuilt as
a full Next.js app for an "enterprise-feeling" UI, **replacing `apps/web`
in place** (old Vite files removed via `git rm`, fully recoverable from
history, not kept alongside). Confirmed scope for this rewrite, so it
doesn't get second-guessed later: **UI-only** - `POST /chat/ask` keeps its
existing single blocking request/response contract (no SSE/streaming was
added), and the only backend change at all was one small additive
endpoint, `GET /users/me` (see below).

- **Stack**: Next.js (App Router) + TypeScript, predominantly client
  components (`"use client"` at the shell/page level) - this app has no
  direct DB access and nothing to gain from RSC data-fetching against the
  FastAPI backend, so Next.js is used for its routing/project conventions,
  not server rendering of agent data. Tailwind CSS v4 + shadcn/ui (Radix
  primitives, `components.json`, "Nova/Lucide/Geist" preset) for the
  component layer, **TanStack Query** for all server state (datasets,
  sessions, session history, versions - `src/hooks/*`), **Zustand**
  (`src/store/chat-store.ts`) for the one real cross-cutting client state
  (current session id, attached/selected dataset ids, the live message
  list, which dataset the Data Explorer is focused on) - the prop-drilling
  `App.tsx` used to do by hand. `next-themes` gives a real manual
  light/dark toggle (the old app only had `prefers-color-scheme`);
  `react-markdown`+`remark-gfm` render assistant text, `react-syntax-
  highlighter` renders code blocks/generated code.
- **Routing** (the actual reason to prefer Next.js over Vite here):
  `/chat` (new-chat landing: dataset picker + prompt box), `/chat/
  [sessionId]` (an active conversation - bookmarkable/shareable, loads via
  `getSessionHistory`), `/datasets` (Data Explorer index: grid, upload),
  `/datasets/[datasetId]` (Schema tab - full column profiles including PII
  badges - and Versions tab, reusing the same version-timeline component
  the old `VersionPanel` was). None of this changes dataset/session
  visibility semantics - see "Dataset visibility" above, still
  listing-scoped only, not real access control.
- **Identity stays the exact same model, just ported**: still no real
  auth. `src/lib/auth.ts`'s `getCurrentUserId()` is the single place
  identity is read from, now sourced from `NEXT_PUBLIC_USER_ID` in
  `apps/web/.env.local` (same gitignored-per-developer pattern,
  `.env.example` is the template). The `(workspace)/layout.tsx` route
  group renders a hard "Access denied" screen instead of the app when it's
  unset - same behavior as the old `App.tsx`, deliberately NOT replaced
  with an in-app identity switcher (a real option that was considered and
  declined - the env-var gate was kept on purpose). When real auth
  eventually replaces this, `getCurrentUserId()` is still the only
  function that needs to change.
- **One small additive backend endpoint**: `GET /users/me?user_id=...`
  (`apps/api/src/api/routes/users.py`) returns `{external_id, role}` via
  the existing `get_or_create_user` - added specifically because the old
  UI had no way to show a user's viewer/editor role or pre-disable an
  action they can't perform; before this, a viewer only found out via a
  403 after clicking Confirm. `src/hooks/use-current-user.ts`'s
  `useCurrentUserRole` wraps it and feeds `transform-card.tsx` (Confirm
  disabled + tooltip for viewers, Reject always enabled - matching the
  backend's actual gate exactly, see "Guardrails" above) and
  `version-timeline.tsx`'s "Make active" button the same way. This
  endpoint never mutates role - a brand-new `user_id` still comes back
  `"viewer"`, same least-privilege default as everywhere else.
- **Result rendering is shape-driven, not schema-driven**: `AskResponse
  .result`/`ChatMessageRecord.result` is genuinely loose-typed on the
  backend (the sandbox's `result` variable can be a scalar, list, dict, or
  the dataframe-preview shape `{type:"dataframe", columns, preview}` - see
  `tools/pii.py`). `components/chat/result-renderer.tsx` branches on shape
  at runtime. It deliberately renders **nothing** for a bare scalar
  result - `agent.graphs.qa`'s `_format_result` already made that exact
  value the whole `answer` text for those routes, so a second copy would
  just be redundant noise; only genuinely additional structured data
  (a dataframe preview, a list of records, a multi-key dict) gets its own
  table/list view.
- **Visual identity carried over deliberately**, not reset to shadcn's
  default neutral theme: the old app's purple accent (`#aa3bff` light /
  `#c084fc` dark) and warm-gray surfaces from `index.css` were ported into
  `globals.css`'s CSS-variable theme (old `--accent` → shadcn's
  `--primary`; shadcn's own `--accent` token stays a subtle hover tint, a
  different concept from the old app's brand-color `--accent`- don't
  conflate the two if you touch this file).
- **Corporate-proxy TLS note, Node side**: the same Zscaler TLS-
  interception issue documented under "Package management" for `uv`
  affects Node/npm tooling too, just with a different fix - `uv`'s
  `system-certs = true` doesn't apply to Node. When a Node-based CLI
  (`shadcn@latest`, etc.) fails with `unable to get local issuer
  certificate`, prefix the command with `NODE_OPTIONS="--use-system-ca"`
  (Node's own equivalent of trusting the OS certificate store) rather than
  disabling TLS verification.
- **`next.config.ts` sets `agentRules: false`**: Next.js 16 auto-generates
  its own `AGENTS.md`/`CLAUDE.md` stub pair on dev-server start otherwise -
  this repo's root `CLAUDE.md` is the single source of truth (see "Repo
  layout" above), so a second, conflicting nested `CLAUDE.md` is
  deliberately suppressed, not an oversight if you notice it's missing.

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
