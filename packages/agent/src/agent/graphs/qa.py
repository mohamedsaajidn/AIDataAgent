import io
import json
import logging
import re
import shutil
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

import pyarrow.parquet as pq
from langgraph.graph import END, StateGraph

logger = logging.getLogger("agent.qa")

from agent import memory
from agent.dataset_files import download_active_version
from agent.llm import get_chat_model, get_response_text
from agent.tracing import get_tracer
from agent.schema_context import (
    build_schema_context,
    describe_datasets_brief,
    find_dataset_by_name,
    get_active_dataset,
    get_column_profiles,
    get_pii_column_names,
    list_other_datasets_brief,
    render_column_descriptions,
)
from core.settings import get_settings
from core.storage import get_s3_client
from sandbox.executor import run_python
from tools.chart_render import build_chart_code, default_title
from tools.chart_spec import SUPPORTED_CHART_TYPES, normalize_chart_type, validate_chart_spec
from tools.pii import redact_pii_from_result
from tools.diff import compute_diff
from tools.eda_render import build_eda_code
from tools.join_render import build_join_code
from tools.join_spec import validate_join_spec

MAX_ATTEMPTS = 3
EXECUTION_TIMEOUT_SECONDS = 20
EDA_TIMEOUT_SECONDS = 45

# Execution artifacts (chart.html/eda.html/result.parquet) are raw bytes,
# often several MB (inline plotly.js alone is ~4MB - see packages/sandbox
# notes in CLAUDE.md for why include_plotlyjs=True is non-negotiable).
# `build_qa_graph()` is a real LangGraph StateGraph, so every node's full
# state gets auto-traced as span input/output once Phase 7 observability is
# on - a multi-MB value living in QAState for even one node (let alone the
# several between execute and respond) is enough to blow past Phoenix's
# OTLP gRPC receive limit and silently drop whole export batches, verified
# live (see CLAUDE.md's "Observability" section - this is the same failure
# class the orchestrator's artifact_ref/_artifact_sidecar exists for).
# `_node_execute` stashes the real bytes here and puts only a small ref +
# the key names (needed by `_node_verify`'s presence checks) into state;
# `ask()` and orchestrator._extract_tool_result (run_route()'s caller) pop
# the real bytes back out here, never through traced state.
_artifact_sidecar: dict[str, dict[str, bytes]] = {}


class QAState(TypedDict, total=False):
    dataset_ids: list[str]
    dataset_id: str
    dataset_name: str
    local_data_path: str
    question: str
    schema_text: str
    base_columns: list[str]
    base_row_count: int
    user_id: str
    session_id: str
    recent_turns_text: str
    consecutive_clarify_count: int
    long_term_facts_text: str
    remembered_fact: str | None
    route: str
    clarification: str | None
    code: str | None
    previous_error: str | None
    attempt: int
    execution_result: dict | None
    artifact_ref: str | None
    artifact_keys: list[str]
    chart_spec: dict | None
    chart_notes: list[str]
    needs_freehand_chart: bool
    join_spec: dict | None
    right_dataset_id: str | None
    right_dataset_name: str | None
    right_local_path: str | None
    diff: dict | None
    pending_parquet_key: str | None
    verified: bool
    verification_error: str | None
    answer: str | None
    final_error: str | None
    prompt_tokens: int
    completion_tokens: int


@dataclass
class QAResult:
    answer: str
    route: str
    code: str | None = None
    result: object = None
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_seconds: float = 0.0
    attempts: int = 0
    artifact_html: str | None = None
    diff: dict | None = None
    pending_parquet_key: str | None = None
    join_spec: dict | None = None
    remembered_fact: str | None = None
    resolved_dataset_id: str | None = None


_RESOLVE_DATASET_PROMPT = """This chat session has multiple datasets attached. Decide which ONE the
user's message is primarily about.

Attached datasets:
{datasets}

User's message: {question}

If the message clearly targets one dataset (by name, or by columns/topics it mentions), respond
with that dataset's id, copied exactly from the list above. If it's genuinely ambiguous - could
reasonably apply to more than one, or names something that matches none of them - ask for
clarification instead of guessing.

Respond with ONLY a JSON object: {{"dataset_id": "..." or null, "clarification_question": "..." or null}}
Exactly one of the two should be non-null.
"""

_MEMORY_EXTRACT_PROMPT = """You detect whether a user's message is correcting or teaching the agent
something about how to interpret this dataset, that should be remembered for future questions -
not just answered once.

Recent conversation in this session:
{recent_turns}

Dataset schema:
{schema}

User's message: {question}

Examples of things to remember: correcting what an EXISTING column means ("region means store
region, not country"), stating a business rule about EXISTING data ("active means status is not
cancelled or refunded"), a standing preference ("always show amounts in thousands"), or any other
clarification that would still be true and useful the next time someone asks a question about
this dataset.

Do NOT flag a message as something to remember if it's just a normal question, even if phrased as
a mild correction of the agent's last answer ("no I meant the total, not the average") without
teaching anything reusable about the dataset itself - that's answered normally, not memorized.

Do NOT flag a message as something to remember if it's asking the agent to DO something to the
data right now - add/drop/rename a column, filter or clean rows, join another dataset, make a
chart, run a profile, etc. - even when it's phrased using "is"/"means" language that sounds like a
definition (e.g. "add a column called seniority_flag that is senior if salary > 90000, else
junior" is a transform request to execute, NOT a fact about what an existing column already
means - "fact" is for describing data that already exists, never for specifying new data to
create). When in doubt about whether something is an action to perform versus a fact to store,
treat it as an action - actions still get a normal answer either way, but a missed action means
the user's request silently never happens.

Respond with ONLY a JSON object:
{{"should_remember": true|false, "fact": "..." or null, "also_asks_question": true|false}}
"fact" must be a short, self-contained sentence - a future prompt won't have this conversation's
context, so name the column/concept explicitly rather than saying "it" or "that". "also_asks_question"
is true if the message ALSO asks something that still needs a normal answer, not just the correction.
"""

_ROUTER_PROMPT = """You are routing a data request to the right approach.

Dataset schema:
{schema}

{memory_context}

Question: {question}

Decide one of:
- "sql": a straightforward aggregate/filter/count/group-by QUESTION that DuckDB SQL handles well (read-only)
- "pandas": a QUESTION needing row-wise logic, text analysis, or something SQL is awkward for (read-only). This also covers a request to explain, interpret, or define a term from an EARLIER answer already visible in the conversation below (e.g. "what does reject H0 mean", "what does that p-value tell us", "can you explain that result") - the code can just set `result` to a plain-language explanation string without touching the dataframe at all if no new computation is needed. Also route here (not "clarify") a short reply like "yes"/"sure"/"go ahead" that is clearly answering something the assistant itself just asked in the conversation below - that means proceed with what was offered, not ask again.
- "chart": the question ONLY asks for a visualization of columns as they already exist (chart, plot, graph, histogram, bar chart, line chart, box plot, "show me", "visualize") - it fills a small structured spec, not arbitrary code, so it can plot existing columns but cannot compute a new value (e.g. a fitted model's metrics) first. A question that needs BOTH a computed value AND a plot of that computation (e.g. "train a model and show MSE, R2, and a scatter of actual vs predicted") is "sql"/"pandas", not "chart" - that code can compute the answer AND optionally save a chart alongside it.
- "eda": asks for an exploratory/profiling overview of the WHOLE dataset ("run EDA", "profile this data", "give me a summary/overview of the dataset")
- "transform": asks to MODIFY the dataset itself - add/drop/rename a column, filter or dedupe rows, change a column's type, fill/drop missing values, bin a column into categories, clean/standardize text, remove outliers, or any other change to the data. This never happens immediately - it always produces a preview for confirmation first, never mutates anything in place.
- "join": asks to COMBINE this dataset with ANOTHER named dataset (merge, join, vlookup, "bring in columns from X", "look up Y from the Z dataset")
- "clarify": too ambiguous to answer confidently against this schema (e.g. references something not clearly identifiable as any column, or genuinely could mean more than one different thing). Do NOT use this just because a question isn't about the dataset's columns at all - explaining/interpreting a prior result (see "pandas" above) is a real, answerable request, not an ambiguous one.

A question asking WHAT something is stays read-only (sql/pandas/chart/eda). A request asking to CHANGE the data is "transform" or "join" - never silently treat a modification request as a read-only question.

Respond with ONLY a JSON object: {{"route": "sql"|"pandas"|"chart"|"eda"|"transform"|"join"|"clarify", "clarification_question": "..."}}
The "clarification_question" key is only needed when route is "clarify" - ask the user specifically what they meant, referencing the closest-matching column names if it helps.
"""

_CODE_PROMPT = """You write short Python data-analysis scripts that run in a sandbox.

Dataset schema:
{schema}

{memory_context}

The dataset is available as a pandas DataFrame: datasets["{dataset_name}"]
pandas is available as `pd`, numpy as `np`, DuckDB as `duckdb` - all pre-imported, no import statements needed for any of them.
If you use DuckDB: it resolves table names via local Python variable names in scope, not arbitrary
expressions inside the SQL text - `datasets["{dataset_name}"]['col']` as a FROM target will fail.
Assign the frame to a plain local variable first, then reference that variable's name in the SQL:
    df = datasets["{dataset_name}"]
    result = duckdb.sql("SELECT COUNT(*) FROM df").fetchone()[0]

Approach to use: {route}

Question: {question}

Rules:
- Assign the final answer to a variable named `result`. Keep it a plain
  number, string, list, or dict - not a matplotlib figure, and never a
  base64-encoded image string.
- If the question also asks for a chart/plot alongside the computed answer
  (e.g. "give me the MSE and a scatter plot of actual vs predicted"), you
  may ADDITIONALLY save one interactive chart with plotly to the exact path
  f"{{ARTIFACTS_DIR}}/chart.html" using fig.write_html(path,
  include_plotlyjs=True) - it will be shown alongside your `result`
  automatically. This is optional and separate from `result` - do not put
  chart data (base64 or otherwise) inside `result` itself.
- Only compute the specific aggregated/filtered answer to the question - do
  not return raw row-level data.
- If a column named in the question already appears in the dataset schema
  above, read it directly (e.g. `df["ThatColumn"]`) - do not recompute,
  redefine, or reconstruct it from scratch using logic remembered from
  earlier conversation. The schema reflects the dataset's actual current
  values; recreated logic can silently drift from what was really persisted
  (e.g. a different default/fallback value) and produce an answer that
  contradicts the real data.
- The schema above already shows each column's current null% - if a column
  you need is already at 0% null, don't add your own defensive cleaning
  step for it; only handle nulls that the schema actually shows.
- Do not touch the network, files outside the sandbox, or subprocesses.
- Respond with ONLY a single ```python fenced code block, no other text.
{retry_context}
"""

_RETRY_CONTEXT_TEMPLATE = """
Your previous attempt failed. Previous code:
```python
{previous_code}
```
Error:
{error}

Fix the bug and write corrected code.
"""

_CHART_SPEC_PROMPT = """You turn a chart request into a small structured spec - you never write plotting code yourself here.

Dataset schema:
{schema}

{memory_context}

Question: {question}

Return ONLY a JSON object with these keys:
- "chart_type": name whatever chart type is actually being requested (e.g. "histogram", "bar", "pie", "donut", "line", "area", "scatter", "box", "violin", or anything else like "radar" or "heatmap" if that's genuinely what was asked - do not force it into one of the common types if it isn't)
- "x": a column name for the x-axis or grouping/category, or null if not applicable
- "y": a numeric column name for the y-axis/value, or null if not needed (e.g. a histogram, or a count-based chart)
- "agg": one of "count", "sum", "mean", "median", or null
- "color": a SECOND categorical column name to split each x value into side-by-side bars/lines by, or null - set this when the question asks to "separate"/"split"/"break down"/"group"/"color" the bars or lines by another column (e.g. "count plot of Geography, separated by whether they Exited" -> x="Geography", color="Exited"). Only meaningful for bar/line/area - leave null for every other chart type. If the question describes this as if it needs two colors/groups per category, this is what expresses that - do NOT try to force it into "x" or "y" instead.
- "filters": a list of {{"column": ..., "op": "eq"|"ne"|"gt"|"gte"|"lt"|"lte"|"in", "value": ...}} objects, or an empty list
- "date_part": one of "year", "month", "quarter", or null - set this when x is a date column and the question asks for a breakdown/trend at a coarser granularity than individual exact dates (e.g. "per year", "by month", "each quarter", "yearly trend"). Only applies to bar/pie/donut/line/area charts. Leave null otherwise - do not set it just because x happens to be a date.
- "title": a short chart title

Guidance for the common types:
- histogram: distribution of ONE continuous numeric column (x). No y.
- bar/pie/donut: a value per category (x = categorical column). No y means count rows per category.
- line/area: a numeric column (y) over an ordered/time column (x), usually a date.
- scatter: relationship between two numeric columns (x and y).
- box/violin: distribution of a numeric column (y), optionally grouped by a categorical column (x).

If the question asks for a SEPARATE chart per column (e.g. "create count plots for Geography and
Gender"), that's more than one chart request - this spec can only ever describe ONE chart, so fill
it in for the first/primary column named only. Never try to cram two columns into "x" and "color"
to fake a second chart - "color" is specifically for splitting ONE chart's bars/lines by a second
column, not for requesting two separate charts.

Respond with ONLY the JSON object, no other text.
{retry_context}
"""

_CHART_RETRY_CONTEXT_TEMPLATE = """
Your previous chart spec didn't work:
{previous_spec}
Issue: {error}

Provide a corrected spec.
"""

_FREEHAND_CHART_PROMPT = """You write Python plotting code that runs in a sandbox. This request needs a
chart type outside the small set with dedicated structured support, so you write the plotting code
directly this time.

Dataset schema:
{schema}

{memory_context}

The dataset is available as a pandas DataFrame: datasets["{dataset_name}"]
pandas is available as `pd`, numpy as `np`, DuckDB as `duckdb` - all pre-imported, no import
statements needed for any of them. plotly.express / plotly.graph_objects are available for
plotting. If you use DuckDB, assign the frame to a plain local variable first and reference that
variable's name in the SQL (`df = datasets["{dataset_name}"]; duckdb.sql("SELECT ... FROM df")`) -
it resolves table names via local Python variable names in scope, not arbitrary expressions in the
SQL text or the dataset's own name.

Question: {question}

Rules:
- Save the chart to f"{{ARTIFACTS_DIR}}/chart.html" using fig.write_html(path, include_plotlyjs=True)
  (always True, never "cdn" - the sandbox's plotly package resolves "cdn" to an incompatible plotly.js build and the chart renders blank)
- Assign a plain string to a variable named `result` that describes exactly what you plotted AND
  explicitly states any assumption you made about anything ambiguous in the request (e.g. which
  column you used for a value the question didn't specify, or how you interpreted something not
  directly asked for). Never make a silent substitution - always say so in `result` if you did.
- Do not touch the network, files outside the sandbox, or subprocesses.
- Respond with ONLY a single ```python fenced code block, no other text.
{retry_context}
"""

_TRANSFORM_PROMPT = """You write Python data-transformation code that runs in a sandbox. This produces a
NEW version of the dataset for the user to preview - the original is never modified, and nothing
becomes permanent until the user explicitly confirms the preview you're about to produce.

Dataset schema:
{schema}

{memory_context}

The dataset is available as a pandas DataFrame: datasets["{dataset_name}"]
pandas is available as `pd`, numpy as `np`, DuckDB as `duckdb` - all pre-imported, no import statements needed for any of them.
If you use DuckDB, assign the frame to a plain local variable first and reference that variable's
name in the SQL (`df = datasets["{dataset_name}"]; duckdb.sql("SELECT ... FROM df")`) - it resolves
table names via local Python variable names in scope, not arbitrary expressions in the SQL text.

Request: {question}

You can do things like:
- Add a computed column (arithmetic, conditional logic via np.where/pd.cut, string concatenation,
  extracting date parts via .dt.year/.dt.month/.dt.day_name(), regex extraction via .str.extract())
- Drop or rename column(s)
- Change a column's type (.astype(...), pd.to_datetime(...), pd.to_numeric(...))
- Filter rows (keep or remove rows matching a condition)
- Handle missing values (.fillna(...) with a constant/mean/median/mode, or .dropna(...))
- Remove duplicate rows (.drop_duplicates(...))
- Bin/bucket a continuous column into categories (pd.cut/pd.qcut)
- Standardize/clean text values (.str.strip(), .str.lower(), .replace(...))
- One-hot encode a categorical column (pd.get_dummies(...))
- Sort rows, or remove outliers (e.g. IQR or z-score based filtering)
- Any other row/column transformation a data analyst would reasonably do with pandas

Rules:
- Start with `df = datasets["{dataset_name}"].copy()` - never mutate the original, work on a copy.
- End by saving the FINAL transformed dataframe: `df.to_parquet(f"{{ARTIFACTS_DIR}}/result.parquet", index=False)`
- Also assign `result` to a short string describing exactly what you did.
- Do not touch the network, files outside the sandbox, or subprocesses.
- Respond with ONLY a single ```python fenced code block, no other text.
{retry_context}
"""

_JOIN_SPEC_PROMPT = """You extract join parameters from a request to combine two datasets - you never
write merge code yourself here.

Current dataset schema:
{schema}

{memory_context}

Other datasets available to join with:
{other_datasets}

Question: {question}

Return ONLY a JSON object:
- "right_dataset_name": the name of the OTHER dataset to join with, copied from the list above
- "left_on": the column name in the CURRENT dataset to join on
- "right_on": the column name in the OTHER dataset to join on (may differ from left_on)
- "how": one of "inner", "left", "right", "outer" - default to "left" if not specified

Respond with ONLY the JSON object, no other text.
{retry_context}
"""

_JOIN_RETRY_CONTEXT_TEMPLATE = """
Your previous join spec didn't work:
{previous_spec}
Issue: {error}

Provide a corrected spec.
"""

_COUNT_KEYWORDS = ("how many", "count", "number of")
_NUMERIC_KEYWORDS = ("average", "mean", "sum", "total", "median")
# Word-boundary, not substring - a plain `"count" in question` also matches
# inside "counts"/"discount"/etc, and the question text can include pasted
# prior-answer context (e.g. "...to get the value counts. <the real ask>"),
# which made this misfire on questions that never actually asked for a count.
_NUMERIC_KEYWORD_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in _COUNT_KEYWORDS + _NUMERIC_KEYWORDS) + r")\b"
)

_ROUTES = ("sql", "pandas", "chart", "eda", "transform", "join", "clarify")


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found in response")
    return json.loads(match.group(0))


def _extract_code(text: str) -> str:
    match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def _track_usage(state: QAState, response) -> None:
    usage = getattr(response, "usage_metadata", None) or {}
    state["prompt_tokens"] = state.get("prompt_tokens", 0) + (usage.get("input_tokens") or 0)
    state["completion_tokens"] = state.get("completion_tokens", 0) + (usage.get("output_tokens") or 0)


def _build_memory_context(state: QAState) -> str:
    parts = []
    if state.get("long_term_facts_text"):
        parts.append(f"Known corrections/preferences from this user for this dataset:\n{state['long_term_facts_text']}")
    if state.get("recent_turns_text"):
        parts.append(f"Recent conversation in this session:\n{state['recent_turns_text']}")
    return "\n\n".join(parts) if parts else "(none yet)"


def _node_resolve_target_dataset(state: QAState) -> QAState:
    dataset_ids = state["dataset_ids"]
    if len(dataset_ids) == 1:
        state["dataset_id"] = dataset_ids[0]
        return state

    brief = describe_datasets_brief(dataset_ids)
    model = get_chat_model(temperature=0.0)
    prompt = _RESOLVE_DATASET_PROMPT.format(datasets=brief, question=state["question"])
    response = model.invoke(prompt)
    _track_usage(state, response)

    try:
        parsed = _extract_json(get_response_text(response))
    except (ValueError, json.JSONDecodeError):
        parsed = {}

    resolved_id = parsed.get("dataset_id")
    if resolved_id not in dataset_ids:
        state["route"] = "clarify"
        state["clarification"] = (
            parsed.get("clarification_question") or "Which of the attached datasets are you asking about?"
        )
        return state

    state["dataset_id"] = resolved_id
    return state


def _node_load_dataset(state: QAState) -> QAState:
    dataset_name, schema_text = build_schema_context(state["dataset_id"])
    _, active_version = get_active_dataset(state["dataset_id"])
    _, local_path = download_active_version(state["dataset_id"])

    state["dataset_name"] = dataset_name
    state["schema_text"] = schema_text
    state["base_columns"] = [c.name for c in active_version.columns]
    state["base_row_count"] = active_version.row_count
    state["local_data_path"] = str(local_path)
    return state


def _node_load_memory(state: QAState) -> QAState:
    turns = memory.get_recent_turns(state.get("session_id"))
    state["recent_turns_text"] = memory.render_recent_turns(turns)

    # How many turns in a row, most-recent-first, were themselves "clarify" -
    # used as a deterministic circuit breaker in _node_plan. Prompt guidance
    # alone (see the router prompt) is a soft fix for why this starts
    # happening; this is the hard backstop that guarantees it can't loop
    # forever even if some other future question hits the same "doesn't fit
    # any route" gap - observed live, a real conversation got stuck
    # re-asking a near-identical clarifying question 3 times in a row even
    # after the user answered "yes" twice.
    consecutive_clarify = 0
    for turn in reversed(turns):
        if turn.get("route") != "clarify":
            break
        consecutive_clarify += 1
    state["consecutive_clarify_count"] = consecutive_clarify

    facts = memory.retrieve_facts(state["user_id"], state["dataset_id"], state["question"])
    state["long_term_facts_text"] = memory.render_facts(facts)
    return state


def _node_extract_memory(state: QAState) -> QAState:
    model = get_chat_model(temperature=0.0)
    prompt = _MEMORY_EXTRACT_PROMPT.format(
        recent_turns=state.get("recent_turns_text") or "(none)",
        schema=state["schema_text"],
        question=state["question"],
    )
    response = model.invoke(prompt)
    _track_usage(state, response)

    try:
        parsed = _extract_json(get_response_text(response))
    except (ValueError, json.JSONDecodeError):
        parsed = {}

    should_remember = bool(parsed.get("should_remember")) and bool(parsed.get("fact"))
    state["remembered_fact"] = parsed.get("fact") if should_remember else None

    if should_remember:
        memory.remember_fact(state["user_id"], state["dataset_id"], parsed["fact"])
        if not parsed.get("also_asks_question", True):
            state["route"] = "memory_ack"

    return state


def _node_save_turn(state: QAState) -> QAState:
    if state.get("session_id"):
        memory.append_turn(state["session_id"], state["question"], state.get("answer") or "", state.get("route") or "")
    return state


def _node_plan(state: QAState) -> QAState:
    model = get_chat_model(temperature=0.0)
    prompt = _ROUTER_PROMPT.format(
        schema=state["schema_text"], memory_context=_build_memory_context(state), question=state["question"]
    )
    response = model.invoke(prompt)
    _track_usage(state, response)

    try:
        parsed = _extract_json(get_response_text(response))
        route = parsed.get("route")
        if route not in _ROUTES:
            route = "pandas"
    except (ValueError, json.JSONDecodeError):
        route, parsed = "pandas", {}

    # Deterministic circuit breaker, not a prompt request - the router
    # choosing "clarify" twice in a row already means asking a third time
    # is not going to work either; force an actual attempt instead of
    # repeating the same unproductive question indefinitely (see
    # _node_load_memory for how this is computed and why).
    if route == "clarify" and state.get("consecutive_clarify_count", 0) >= 2:
        logger.warning(
            "route=clarify chosen again after %d consecutive clarifies - overriding to pandas to break the loop",
            state["consecutive_clarify_count"],
        )
        route = "pandas"

    state["route"] = route
    state["clarification"] = parsed.get("clarification_question") if route == "clarify" else None
    logger.info("route=%s question=%r", route, state["question"][:120])
    return state


def _node_generate_code(state: QAState) -> QAState:
    retry_context = ""
    if state.get("previous_error") and state.get("code"):
        retry_context = _RETRY_CONTEXT_TEMPLATE.format(previous_code=state["code"], error=state["previous_error"])

    model = get_chat_model(temperature=0.0)
    prompt = _CODE_PROMPT.format(
        schema=state["schema_text"],
        memory_context=_build_memory_context(state),
        dataset_name=state["dataset_name"],
        route=state["route"],
        question=state["question"],
        retry_context=retry_context,
    )
    response = model.invoke(prompt)
    _track_usage(state, response)
    state["code"] = _extract_code(get_response_text(response))
    state["attempt"] = state.get("attempt", 0) + 1
    return state


def _node_generate_chart_spec(state: QAState) -> QAState:
    retry_context = ""
    if state.get("previous_error") and state.get("chart_spec"):
        retry_context = _CHART_RETRY_CONTEXT_TEMPLATE.format(
            previous_spec=json.dumps(state["chart_spec"]), error=state["previous_error"]
        )

    model = get_chat_model(temperature=0.0)
    prompt = _CHART_SPEC_PROMPT.format(
        schema=state["schema_text"],
        memory_context=_build_memory_context(state),
        question=state["question"],
        retry_context=retry_context,
    )
    response = model.invoke(prompt)
    _track_usage(state, response)

    try:
        spec = _extract_json(get_response_text(response))
    except (ValueError, json.JSONDecodeError):
        spec = {"chart_type": None}

    state["chart_spec"] = spec
    state["attempt"] = state.get("attempt", 0) + 1
    logger.info("chart_spec attempt=%d spec=%s", state["attempt"], spec)
    return state


def _node_validate_chart(state: QAState) -> QAState:
    requested_type = normalize_chart_type((state.get("chart_spec") or {}).get("chart_type"))
    if requested_type not in SUPPORTED_CHART_TYPES:
        # Not one we structurally validate/render - fall back to freehand
        # generation for this specific request rather than rejecting it.
        logger.info("chart_type=%r unsupported -> falling back to freehand", requested_type)
        state["chart_spec"] = None
        state["chart_notes"] = []
        state["needs_freehand_chart"] = True
        return state

    columns = get_column_profiles(state["dataset_id"])
    corrected, notes = validate_chart_spec(state["chart_spec"], columns)
    if corrected is None:
        # The structured spec (one x, one y) genuinely can't represent this
        # request - observed live: "box plot for all 8 numeric columns" made
        # the spec-filling LLM invent nonexistent column names ('variable',
        # 'numeric_column') trying to force a many-columns-at-once request
        # into a single x/y, and this used to dead-end here immediately with
        # that validation note as the final answer - no retry, no fallback,
        # even though chart_freehand (arbitrary code) handles the identical
        # multi-column pattern correctly, proven by it already working for
        # unsupported chart types like KDE. A supported chart_type whose
        # spec can't validate gets the same fallback an unsupported one
        # already gets above, instead of a dead end.
        logger.info("chart_spec validation failed, unsalvageable -> falling back to freehand")
        state["chart_spec"] = None
        state["chart_notes"] = []
        state["needs_freehand_chart"] = True
        return state
    if notes:
        logger.info("chart_spec auto-corrected: %s", notes)
    state["chart_spec"] = corrected
    state["chart_notes"] = notes
    state["needs_freehand_chart"] = False
    return state


def _node_generate_freehand_chart(state: QAState) -> QAState:
    retry_context = ""
    if state.get("previous_error") and state.get("code"):
        retry_context = _RETRY_CONTEXT_TEMPLATE.format(previous_code=state["code"], error=state["previous_error"])

    model = get_chat_model(temperature=0.0)
    prompt = _FREEHAND_CHART_PROMPT.format(
        schema=state["schema_text"],
        memory_context=_build_memory_context(state),
        dataset_name=state["dataset_name"],
        question=state["question"],
        retry_context=retry_context,
    )
    response = model.invoke(prompt)
    _track_usage(state, response)
    state["code"] = _extract_code(get_response_text(response))
    state["attempt"] = state.get("attempt", 0) + 1
    return state


def _node_prepare_chart_code(state: QAState) -> QAState:
    state["code"] = build_chart_code(state["chart_spec"], state["dataset_name"])
    return state


def _node_run_eda(state: QAState) -> QAState:
    # Deterministic - no LLM call, no ambiguity to resolve for "profile this dataset".
    state["code"] = build_eda_code(state["dataset_name"])
    state["attempt"] = state.get("attempt", 0) + 1
    return state


def _node_generate_transform(state: QAState) -> QAState:
    retry_context = ""
    if state.get("previous_error") and state.get("code"):
        retry_context = _RETRY_CONTEXT_TEMPLATE.format(previous_code=state["code"], error=state["previous_error"])

    model = get_chat_model(temperature=0.0)
    prompt = _TRANSFORM_PROMPT.format(
        schema=state["schema_text"],
        memory_context=_build_memory_context(state),
        dataset_name=state["dataset_name"],
        question=state["question"],
        retry_context=retry_context,
    )
    response = model.invoke(prompt)
    _track_usage(state, response)
    state["code"] = _extract_code(get_response_text(response))
    state["attempt"] = state.get("attempt", 0) + 1
    return state


def _node_generate_join_spec(state: QAState) -> QAState:
    retry_context = ""
    if state.get("previous_error") and state.get("join_spec"):
        retry_context = _JOIN_RETRY_CONTEXT_TEMPLATE.format(
            previous_spec=json.dumps(state["join_spec"]), error=state["previous_error"]
        )

    other_datasets = list_other_datasets_brief(state["dataset_id"])
    model = get_chat_model(temperature=0.0)
    prompt = _JOIN_SPEC_PROMPT.format(
        schema=state["schema_text"],
        memory_context=_build_memory_context(state),
        other_datasets=other_datasets,
        question=state["question"],
        retry_context=retry_context,
    )
    response = model.invoke(prompt)
    _track_usage(state, response)

    try:
        spec = _extract_json(get_response_text(response))
    except (ValueError, json.JSONDecodeError):
        spec = {}

    state["join_spec"] = spec
    state["attempt"] = state.get("attempt", 0) + 1
    return state


def _node_resolve_join_dataset(state: QAState) -> QAState:
    name_hint = (state.get("join_spec") or {}).get("right_dataset_name")
    if not name_hint:
        state["join_spec"] = None
        state["verification_error"] = "Could not determine which dataset to join with."
        return state

    right_dataset = find_dataset_by_name(name_hint, state["dataset_id"])
    if not right_dataset:
        state["join_spec"] = None
        state["verification_error"] = f"Could not find a dataset matching '{name_hint}' to join with."
        return state

    right_name, right_path = download_active_version(str(right_dataset.id))
    state["right_dataset_id"] = str(right_dataset.id)
    state["right_dataset_name"] = right_name
    state["right_local_path"] = str(right_path)
    return state


def _node_validate_join(state: QAState) -> QAState:
    left_columns = set(get_column_profiles(state["dataset_id"]).keys())
    right_columns = set(get_column_profiles(state["right_dataset_id"]).keys())
    corrected, notes = validate_join_spec(state["join_spec"], left_columns, right_columns)
    state["join_spec"] = corrected
    if corrected is None:
        state["verification_error"] = "; ".join(notes) if notes else "That join isn't valid for these datasets."
    return state


def _node_prepare_join_code(state: QAState) -> QAState:
    state["code"] = build_join_code(state["join_spec"], state["dataset_name"], state["right_dataset_name"])
    return state


def _collect_pii_values(path: Path, pii_columns: set[str], cap: int = 5000) -> set[str]:
    """Reads just the PII-flagged columns' actual values from the local
    dataset file (host-side - this runs in the trusted API/agent process,
    never inside the sandbox) so redact_pii_from_result can catch a leak
    that carries no column name at all, e.g. `df["region"].unique().tolist()`
    - a bare list of strings with nothing in the structure identifying which
    column they came from. Verified live: without this, that exact pattern
    let a PII-flagged column's raw values reach the final answer untouched;
    see redact_pii_from_result's docstring for the by-name check this
    complements. Capped so a high-cardinality PII column (free text, an
    id-like field) can't make this scan itself expensive - a capped miss
    just means that particular value isn't caught by the value-level check,
    not that PII protection silently vanishes (the schema-text warning to
    the code-gen LLM and the by-name check both still apply regardless).
    """
    if not pii_columns:
        return set()
    try:
        table = pq.read_table(path, columns=list(pii_columns))
    except Exception:
        logger.warning("could not read PII columns from %s for redaction value-scan", path, exc_info=True)
        return set()
    values: set[str] = set()
    for col in table.column_names:
        for v in table.column(col).to_pylist():
            if v is not None:
                values.add(str(v))
            if len(values) >= cap:
                return values
    return values


def _node_execute(state: QAState) -> QAState:
    datasets = {state["dataset_name"]: Path(state["local_data_path"])}
    if state.get("right_dataset_name") and state.get("right_local_path"):
        datasets[state["right_dataset_name"]] = Path(state["right_local_path"])

    timeout = EDA_TIMEOUT_SECONDS if state["route"] == "eda" else EXECUTION_TIMEOUT_SECONDS
    logger.info("executing code (attempt=%s, %d chars):\n%s", state.get("attempt"), len(state["code"] or ""), state["code"])

    # sandbox.executor.run_python is a raw subprocess call, not a LangChain/
    # LangGraph construct - LangChainInstrumentor never sees it, so it gets
    # an explicit span here (see agent.tracing's module docstring for why
    # this is the one node that needs manual instrumentation). No-ops via
    # nullcontext when Phoenix is disabled, so this is never load-bearing.
    tracer = get_tracer()
    span_cm = tracer.start_as_current_span("sandbox.execute") if tracer is not None else nullcontext()
    with span_cm as span:
        res = run_python(state["code"], datasets, timeout_seconds=timeout)
        if span is not None:
            span.set_attribute("route", state["route"])
            span.set_attribute("attempt", state.get("attempt", 0))
            span.set_attribute("timeout_seconds", timeout)
            span.set_attribute("success", res.error is None)
            span.set_attribute("timed_out", bool(res.timed_out))
            span.set_attribute("oom_killed", bool(res.oom_killed))
            span.set_attribute("rejected", bool(res.rejected))
            if res.error:
                span.set_attribute("error", res.error)

    # See _artifact_sidecar's module-level docstring for why the real bytes
    # never go directly into traced state.
    artifact_ref = uuid.uuid4().hex
    _artifact_sidecar[artifact_ref] = res.artifacts or {}
    state["artifact_ref"] = artifact_ref
    state["artifact_keys"] = list((res.artifacts or {}).keys())

    # Enforcement, not a prompt hint - see redact_pii_from_result's own
    # docstring for why this can't just rely on the code-gen prompt's
    # warning. A join can pull PII columns in from either side, so both
    # datasets' flagged columns apply here, not just the primary one.
    pii_columns = get_pii_column_names(state["dataset_id"])
    pii_values = _collect_pii_values(Path(state["local_data_path"]), pii_columns)
    if state.get("right_dataset_id"):
        right_pii_columns = get_pii_column_names(state["right_dataset_id"])
        pii_columns = pii_columns | right_pii_columns
        if state.get("right_local_path"):
            pii_values |= _collect_pii_values(Path(state["right_local_path"]), right_pii_columns)
    result = redact_pii_from_result(res.result, pii_columns, pii_values)

    state["execution_result"] = {
        "result": result,
        "error": res.error,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "timed_out": res.timed_out,
        "rejected": res.rejected,
        "oom_killed": res.oom_killed,
    }
    if res.error:
        logger.info("execution error: %s", res.error)
    else:
        logger.info(
            "execution ok: result_type=%s artifacts=%s", type(res.result).__name__, list(res.artifacts or {})
        )
    return state


def _looks_numeric(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, dict):
        return any(_looks_numeric(v) for v in value.values())
    if isinstance(value, list):
        return any(_looks_numeric(v) for v in value)
    return False


def _node_verify(state: QAState) -> QAState:
    exec_result = state["execution_result"]
    if exec_result["error"]:
        state["verified"] = False
        state["verification_error"] = exec_result["error"]
        return state

    if state["route"] == "chart":
        if "chart.html" not in (state.get("artifact_keys") or []):
            state["verified"] = False
            # Actionable, not just descriptive - a generic "no artifact"
            # message left retries guessing at a cause (observed: the model
            # started using os.getenv("ARTIFACTS_DIR") with a fallback path
            # on a later attempt, since it's a plain namespace variable, not
            # an env var - that silently wrote the file to the wrong place).
            state["verification_error"] = (
                'The chart code ran but did not produce a chart.html artifact. Save it to the exact path '
                'f"{ARTIFACTS_DIR}/chart.html" - ARTIFACTS_DIR is already a plain string variable in your '
                "script's namespace, not an environment variable. Do not use os.getenv/os.environ or guess "
                "a fallback path."
            )
            return state
        state["verified"] = True
        state["verification_error"] = None
        return state

    if state["route"] == "eda":
        if "eda.html" not in (state.get("artifact_keys") or []):
            state["verified"] = False
            state["verification_error"] = "The EDA code ran but did not produce a report."
            return state
        state["verified"] = True
        state["verification_error"] = None
        return state

    if state["route"] in ("transform", "join"):
        if "result.parquet" not in (state.get("artifact_keys") or []):
            state["verified"] = False
            state["verification_error"] = "The code ran but did not produce a result.parquet artifact."
            return state
        state["verified"] = True
        state["verification_error"] = None
        return state

    result = exec_result["result"]
    question = state["question"].lower()
    if result is None:
        state["verified"] = False
        state["verification_error"] = "The code ran but did not set a `result` variable."
        return state

    # An empty list/dict is a legitimate "nothing matched" answer regardless
    # of question phrasing - retrying reruns the same code against the same
    # data and gets the same empty result every time, so rejecting it here
    # just burns retries (and MAX_ATTEMPTS) without ever being able to fix it.
    is_empty_container = isinstance(result, (list, dict)) and len(result) == 0

    wants_number = not is_empty_container and bool(_NUMERIC_KEYWORD_PATTERN.search(question))
    if wants_number and not _looks_numeric(result):
        state["verified"] = False
        state["verification_error"] = (
            f"The question expects a number, but the result was {result!r}, which doesn't look numeric."
        )
        return state

    state["verified"] = True
    state["verification_error"] = None
    return state


def _should_retry(state: QAState) -> Literal["retry", "finalize", "respond"]:
    if state.get("verified"):
        logger.info("verified ok (attempt=%s)", state.get("attempt"))
        return "finalize" if state["route"] in ("transform", "join") else "respond"
    if state["route"] == "eda":
        logger.info("verify failed, eda is deterministic so not retrying: %s", state.get("verification_error"))
        return "respond"  # deterministic code - retrying identically won't change the outcome
    decision = "retry" if state.get("attempt", 0) < MAX_ATTEMPTS else "respond"
    logger.info(
        "verify failed (attempt=%s/%s) -> %s, reason: %s",
        state.get("attempt"), MAX_ATTEMPTS, decision, state.get("verification_error"),
    )
    return decision


def _node_prepare_retry(state: QAState) -> QAState:
    state["previous_error"] = state.get("verification_error")
    return state


def _node_finalize_transform(state: QAState) -> QAState:
    """Computes the diff and uploads the proposed result to a pending (not
    yet versioned) S3 location - nothing here touches the catalog. Creating
    the actual PendingTransform row is the API layer's job (same split as
    everywhere else in this codebase: agent graphs return data, apps/api
    services persist it).
    """
    artifacts = _artifact_sidecar.get(state.get("artifact_ref"), {})
    parquet_bytes = artifacts.get("result.parquet")
    if not parquet_bytes:
        # Shouldn't happen - verify() already checked this before routing
        # here - but don't claim success if it somehow does.
        state["verified"] = False
        state["verification_error"] = "No result.parquet artifact was produced."
        return state

    parquet_file = pq.ParquetFile(io.BytesIO(parquet_bytes))
    new_columns = list(parquet_file.schema.names)
    new_row_count = parquet_file.metadata.num_rows

    state["diff"] = compute_diff(state["base_columns"], new_columns, state["base_row_count"], new_row_count)

    settings = get_settings()
    s3 = get_s3_client()
    pending_key = f"datasets/{state['dataset_id']}/pending/{uuid.uuid4()}/data.parquet"
    s3.put_object(Bucket=settings.s3_bucket, Key=pending_key, Body=parquet_bytes)
    state["pending_parquet_key"] = pending_key
    return state


def _format_result(result: object) -> str:
    if isinstance(result, dict) and result.get("type") == "dataframe":
        preview = result.get("preview", [])
        cols = ", ".join(result.get("columns", []))
        return f"{result.get('row_count')} row(s) with columns [{cols}]. Preview: {json.dumps(preview[:5])}"
    if isinstance(result, (dict, list)):
        return json.dumps(result)
    return str(result)


def _node_respond(state: QAState) -> QAState:
    if state["route"] == "memory_ack":
        state["answer"] = f"Got it — I'll remember that {state['remembered_fact'].rstrip('.')}."
        return state

    state = _node_respond_answer(state)

    if state.get("remembered_fact"):
        fact = state["remembered_fact"].rstrip(".")
        state["answer"] = f"Got it — I'll remember that {fact}. {state['answer']}"

    return state


def _node_respond_answer(state: QAState) -> QAState:
    if state["route"] == "clarify":
        state["answer"] = state.get("clarification") or "Could you clarify what you mean?"
        return state

    if state["route"] == "chart" and not state.get("needs_freehand_chart") and state.get("execution_result") is None:
        # validate_chart couldn't salvage the structured spec at all - never reached execute.
        # As of the needs_freehand_chart fix above this should be rare (an
        # unsalvageable spec now falls back to freehand instead of landing
        # here) - if this still fires, freehand didn't run either, worth a
        # closer look.
        reason = " ".join(state.get("chart_notes") or []) or "I couldn't map that to a valid chart for this data."
        logger.info("chart dead-end (no freehand fallback reached): %s", reason)
        state["answer"] = reason
        state["final_error"] = reason
        return state

    if state["route"] == "join" and state.get("execution_result") is None:
        # resolve_join_dataset or validate_join failed outright - never reached
        # execute. Unlike chart, join has no freehand fallback to reach for,
        # so this dead end is legitimate - still logged so it's visible why.
        reason = state.get("verification_error") or "I couldn't set up that join."
        logger.info("join dead-end (no freehand alternative exists): %s", reason)
        state["answer"] = reason
        state["final_error"] = reason
        return state

    if state["route"] in ("transform", "join") and state.get("verified") and not state.get("diff"):
        # finalize_transform failed unexpectedly after a successful verify - rare, but never claim success.
        state["answer"] = "Something went wrong while preparing the preview. Please try again."
        state["final_error"] = "finalize failed after a successful execution"
        return state

    if not state.get("verified"):
        subject = "chart" if state["route"] == "chart" else ("preview" if state["route"] in ("transform", "join") else "answer")
        state["answer"] = (
            f"I tried a few times but couldn't produce a reliable {subject} for that request. "
            f"Last issue: {state.get('verification_error')}"
        )
        state["final_error"] = state.get("verification_error")
        return state

    if state["route"] == "chart" and state.get("chart_spec"):
        notes = state.get("chart_notes") or []
        prefix = (" ".join(notes) + " ") if notes else ""
        spec = state["chart_spec"]
        state["answer"] = f"{prefix}Here's a {spec['chart_type']} chart: {spec.get('title') or default_title(spec)}."
        return state

    if state["route"] == "eda":
        result = state["execution_result"]["result"] or {}
        # The HTML report is stats/profiling only (types, nulls, distributions)
        # - it never carries the per-column semantic descriptions written once
        # at upload time. A user asking "what does each column mean" wants
        # those, not (only) the profiling report, so surface both here rather
        # than making that a separate, harder-to-route request.
        state["answer"] = (
            f"EDA report generated: {result.get('rows', '?')} rows, {result.get('columns', '?')} columns, "
            f"{result.get('duplicate_rows', '?')} duplicate row(s).\n\n"
            f"Column descriptions:\n{render_column_descriptions(state['dataset_id'])}"
        )
        return state

    if state["route"] in ("transform", "join"):
        diff = state["diff"]
        state["answer"] = (
            f"Preview (not yet applied): {diff['summary']} "
            "Confirm this change to apply it, or ask something else to discard the preview."
        )
        return state

    # Freehand chart, or a plain sql/pandas answer - both already produced a
    # self-describing `result` (the freehand prompt requires stating any
    # assumptions made), so there's nothing extra to add here.
    state["answer"] = _format_result(state["execution_result"]["result"])
    return state


def _effective_route(planned_route: str, code: str | None, needs_freehand_chart: bool = False) -> str:
    """`plan` picks sql/pandas before any code exists - it's a hint fed into
    the generate_code prompt, never enforced. The model is free to ignore it
    (e.g. use plain pandas for a filter the router guessed was SQL-shaped).
    Report what the accepted code actually does, not the pre-code guess -
    the "route" field should describe reality, not planning intent.

    A chart handled by the freehand fallback (an unsupported chart type)
    is tagged distinctly so it's always auditable which charts went through
    real validation versus best-effort generation.
    """
    if planned_route == "chart" and needs_freehand_chart:
        return "chart_freehand"
    if planned_route in ("clarify", "chart", "eda", "transform", "join") or not code:
        return planned_route
    return "sql" if "duckdb" in code else "pandas"


def _route_from_plan(state: QAState) -> str:
    route = state["route"]
    if route == "clarify":
        return "respond"
    if route == "chart":
        return "generate_chart_spec"
    if route == "eda":
        return "run_eda"
    if route == "transform":
        return "generate_transform"
    if route == "join":
        return "generate_join_spec"
    return "generate_code"


def _route_after_validate_chart(state: QAState) -> str:
    if state.get("needs_freehand_chart"):
        return "generate_freehand_chart"
    return "prepare_chart_code" if state.get("chart_spec") else "respond"


def _route_after_resolve_join(state: QAState) -> str:
    return "validate_join" if state.get("right_dataset_name") else "respond"


def _route_after_validate_join(state: QAState) -> str:
    return "prepare_join_code" if state.get("join_spec") else "respond"


def _route_after_prepare_retry(state: QAState) -> str:
    if state.get("needs_freehand_chart"):
        return "generate_freehand_chart"
    route = state.get("route")
    if route == "chart":
        return "generate_chart_spec"
    if route == "join":
        return "generate_join_spec"
    if route == "transform":
        return "generate_transform"
    return "generate_code"


# Node-name -> node-function and node-name -> next-node-name tables mirroring
# build_qa_graph()'s wiring exactly (same functions, not a reimplementation).
# Used by run_route() below to replay one route's node sequence outside the
# compiled graph - see run_route's own docstring for why that exists.
_NODE_FUNCS = {
    "generate_code": _node_generate_code,
    "generate_chart_spec": _node_generate_chart_spec,
    "validate_chart": _node_validate_chart,
    "prepare_chart_code": _node_prepare_chart_code,
    "generate_freehand_chart": _node_generate_freehand_chart,
    "run_eda": _node_run_eda,
    "generate_transform": _node_generate_transform,
    "generate_join_spec": _node_generate_join_spec,
    "resolve_join_dataset": _node_resolve_join_dataset,
    "validate_join": _node_validate_join,
    "prepare_join_code": _node_prepare_join_code,
    "execute": _node_execute,
    "verify": _node_verify,
    "prepare_retry": _node_prepare_retry,
    "finalize_transform": _node_finalize_transform,
}


def _route_after_verify(state: QAState) -> str:
    decision = _should_retry(state)
    return {"retry": "prepare_retry", "finalize": "finalize_transform", "respond": "respond"}[decision]


_EDGE_FUNCS = {
    "generate_code": lambda s: "execute",
    "generate_transform": lambda s: "execute",
    "run_eda": lambda s: "execute",
    "generate_chart_spec": lambda s: "validate_chart",
    "validate_chart": _route_after_validate_chart,
    "prepare_chart_code": lambda s: "execute",
    "generate_freehand_chart": lambda s: "execute",
    "generate_join_spec": lambda s: "resolve_join_dataset",
    "resolve_join_dataset": _route_after_resolve_join,
    "validate_join": _route_after_validate_join,
    "prepare_join_code": lambda s: "execute",
    "execute": lambda s: "verify",
    "verify": _route_after_verify,
    "prepare_retry": _route_after_prepare_retry,
    "finalize_transform": lambda s: "respond",
}


def run_route(route: str, base_state: QAState) -> QAState:
    """Drives a QAState through the same node sequence build_qa_graph() would
    for a given route, starting past the router/dataset/memory preamble -
    used by the tool-calling orchestrator graph (agent.graphs.orchestrator),
    whose orchestrator node already decided which capability to use (via
    tool selection) and already loaded dataset/memory context itself, so
    there's no router LLM call to make here and no plan/resolve_dataset/
    load_memory nodes to run again.

    This replays _NODE_FUNCS/_EDGE_FUNCS - the exact functions
    build_qa_graph() wires up, not a reimplementation - specifically so
    every fix made to the main graph (retry behavior, chart freehand
    fallback, verify heuristics, ARTIFACTS_DIR handling, etc.) applies here
    automatically with no risk of the two call paths drifting apart. If you
    add a new route or change routing logic in build_qa_graph(), update the
    corresponding _route_after_* function here too - they must stay in sync.
    """
    state: QAState = dict(base_state)
    state["route"] = route
    node = _route_from_plan(state)
    while node != "respond":
        state = _NODE_FUNCS[node](state)
        node = _EDGE_FUNCS[node](state)
    return _node_respond_answer(state)


def build_qa_graph():
    graph = StateGraph(QAState)
    graph.add_node("resolve_target_dataset", _node_resolve_target_dataset)
    graph.add_node("load_dataset", _node_load_dataset)
    graph.add_node("load_memory", _node_load_memory)
    graph.add_node("extract_memory", _node_extract_memory)
    graph.add_node("save_turn", _node_save_turn)
    graph.add_node("plan", _node_plan)
    graph.add_node("generate_code", _node_generate_code)
    graph.add_node("generate_chart_spec", _node_generate_chart_spec)
    graph.add_node("validate_chart", _node_validate_chart)
    graph.add_node("prepare_chart_code", _node_prepare_chart_code)
    graph.add_node("generate_freehand_chart", _node_generate_freehand_chart)
    graph.add_node("run_eda", _node_run_eda)
    graph.add_node("generate_transform", _node_generate_transform)
    graph.add_node("generate_join_spec", _node_generate_join_spec)
    graph.add_node("resolve_join_dataset", _node_resolve_join_dataset)
    graph.add_node("validate_join", _node_validate_join)
    graph.add_node("prepare_join_code", _node_prepare_join_code)
    graph.add_node("execute", _node_execute)
    graph.add_node("verify", _node_verify)
    graph.add_node("prepare_retry", _node_prepare_retry)
    graph.add_node("finalize_transform", _node_finalize_transform)
    graph.add_node("respond", _node_respond)

    graph.set_entry_point("resolve_target_dataset")
    graph.add_conditional_edges(
        "resolve_target_dataset", lambda s: "respond" if s.get("route") == "clarify" else "load_dataset"
    )
    graph.add_edge("load_dataset", "load_memory")
    graph.add_edge("load_memory", "extract_memory")
    graph.add_conditional_edges(
        "extract_memory", lambda s: "respond" if s.get("route") == "memory_ack" else "plan"
    )

    graph.add_conditional_edges("plan", _route_from_plan)
    graph.add_edge("generate_code", "execute")
    graph.add_edge("generate_transform", "execute")
    graph.add_edge("run_eda", "execute")

    graph.add_edge("generate_chart_spec", "validate_chart")
    graph.add_conditional_edges("validate_chart", _route_after_validate_chart)
    graph.add_edge("prepare_chart_code", "execute")
    graph.add_edge("generate_freehand_chart", "execute")

    graph.add_edge("generate_join_spec", "resolve_join_dataset")
    graph.add_conditional_edges("resolve_join_dataset", _route_after_resolve_join)
    graph.add_conditional_edges("validate_join", _route_after_validate_join)
    graph.add_edge("prepare_join_code", "execute")

    graph.add_edge("execute", "verify")
    graph.add_conditional_edges(
        "verify", _should_retry, {"retry": "prepare_retry", "finalize": "finalize_transform", "respond": "respond"}
    )
    graph.add_edge("finalize_transform", "respond")
    graph.add_edge("respond", "save_turn")
    graph.add_conditional_edges("prepare_retry", _route_after_prepare_retry)
    graph.add_edge("save_turn", END)

    return graph.compile()


def ask(dataset_ids: list[str], question: str, user_id: str, session_id: str) -> QAResult:
    """`dataset_ids` are every dataset attached to this chat session - which
    ONE a given question is actually about is resolved inside the graph
    (`_node_resolve_target_dataset`), not here, since that resolution needs
    an LLM call when there's more than one and this function shouldn't
    duplicate that decision.
    """
    graph = build_qa_graph()
    logger.info("ask() start: user_id=%s session_id=%s dataset_ids=%s question=%r", user_id, session_id, dataset_ids, question)
    start = time.monotonic()
    # See agent.graphs.orchestrator.ask()'s identical wrapping for why this
    # exists - without an explicit outer span, _node_execute's manual
    # sandbox.execute span has no ambient parent to attach to and comes out
    # as its own orphaned trace (verified live) instead of part of this
    # question's trace.
    tracer = get_tracer()
    span_cm = tracer.start_as_current_span("qa.ask") if tracer is not None else nullcontext()
    with span_cm:
        final_state: QAState = graph.invoke(
            {
                "dataset_ids": dataset_ids,
                "question": question,
                "user_id": user_id,
                "session_id": session_id,
                "attempt": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }
        )
    duration = time.monotonic() - start

    local_path = final_state.get("local_data_path")
    right_local_path = final_state.get("right_local_path")
    if local_path:
        shutil.rmtree(Path(local_path).parent, ignore_errors=True)
    if right_local_path:
        shutil.rmtree(Path(right_local_path).parent, ignore_errors=True)

    exec_result = final_state.get("execution_result") or {}
    # Popped, not just read - see _artifact_sidecar's docstring; nothing
    # else needs this entry once ask() has returned.
    artifacts = _artifact_sidecar.pop(final_state.get("artifact_ref"), {})
    artifact_bytes = artifacts.get("chart.html") or artifacts.get("eda.html")
    effective_route = _effective_route(
        final_state.get("route", ""), final_state.get("code"), final_state.get("needs_freehand_chart", False)
    )
    final_error = final_state.get("final_error")

    # The single line worth grepping the terminal for - a 200 response tells
    # you nothing about whether the agent actually succeeded (a graceful
    # "I couldn't do that" is still HTTP 200). This is a WARNING when there
    # was a final_error specifically so it's visually distinct from the
    # normal INFO trace above it while actively developing.
    log = logger.warning if final_error else logger.info
    log(
        "ask() done: route=%s effective_route=%s attempts=%s duration=%.1fs has_artifact=%s error=%r",
        final_state.get("route"),
        effective_route,
        final_state.get("attempt", 0),
        duration,
        bool(artifact_bytes),
        final_error,
    )

    return QAResult(
        answer=final_state.get("answer", ""),
        route=effective_route,
        code=final_state.get("code"),
        result=exec_result.get("result"),
        error=final_error,
        prompt_tokens=final_state.get("prompt_tokens", 0),
        completion_tokens=final_state.get("completion_tokens", 0),
        duration_seconds=duration,
        attempts=final_state.get("attempt", 0),
        artifact_html=artifact_bytes.decode("utf-8") if artifact_bytes else None,
        diff=final_state.get("diff"),
        pending_parquet_key=final_state.get("pending_parquet_key"),
        join_spec=final_state.get("join_spec"),
        remembered_fact=final_state.get("remembered_fact"),
        resolved_dataset_id=final_state.get("dataset_id"),
    )
