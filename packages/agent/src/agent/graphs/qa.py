import io
import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

import pyarrow.parquet as pq
from langgraph.graph import END, StateGraph

from agent.dataset_files import download_active_version
from agent.llm import get_chat_model
from agent.schema_context import (
    build_schema_context,
    find_dataset_by_name,
    get_active_dataset,
    get_column_profiles,
    list_other_datasets_brief,
)
from core.settings import get_settings
from core.storage import get_s3_client
from sandbox.executor import run_python
from tools.chart_render import build_chart_code, default_title
from tools.chart_spec import SUPPORTED_CHART_TYPES, normalize_chart_type, validate_chart_spec
from tools.diff import compute_diff
from tools.eda_render import build_eda_code
from tools.join_render import build_join_code
from tools.join_spec import validate_join_spec

MAX_ATTEMPTS = 3
EXECUTION_TIMEOUT_SECONDS = 20
EDA_TIMEOUT_SECONDS = 45


class QAState(TypedDict, total=False):
    dataset_id: str
    dataset_name: str
    local_data_path: str
    question: str
    schema_text: str
    base_columns: list[str]
    base_row_count: int
    route: str
    clarification: str | None
    code: str | None
    previous_error: str | None
    attempt: int
    execution_result: dict | None
    artifacts: dict[str, bytes]
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


_ROUTER_PROMPT = """You are routing a data request to the right approach.

Dataset schema:
{schema}

Question: {question}

Decide one of:
- "sql": a straightforward aggregate/filter/count/group-by QUESTION that DuckDB SQL handles well (read-only)
- "pandas": a QUESTION needing row-wise logic, text analysis, or something SQL is awkward for (read-only)
- "chart": the question asks for a visualization (chart, plot, graph, histogram, bar chart, line chart, box plot, "show me", "visualize")
- "eda": asks for an exploratory/profiling overview of the WHOLE dataset ("run EDA", "profile this data", "give me a summary/overview of the dataset")
- "transform": asks to MODIFY the dataset itself - add/drop/rename a column, filter or dedupe rows, change a column's type, fill/drop missing values, bin a column into categories, clean/standardize text, remove outliers, or any other change to the data. This never happens immediately - it always produces a preview for confirmation first, never mutates anything in place.
- "join": asks to COMBINE this dataset with ANOTHER named dataset (merge, join, vlookup, "bring in columns from X", "look up Y from the Z dataset")
- "clarify": too ambiguous to answer confidently against this schema (e.g. references something not clearly identifiable as any column)

A question asking WHAT something is stays read-only (sql/pandas/chart/eda). A request asking to CHANGE the data is "transform" or "join" - never silently treat a modification request as a read-only question.

Respond with ONLY a JSON object: {{"route": "sql"|"pandas"|"chart"|"eda"|"transform"|"join"|"clarify", "clarification_question": "..."}}
The "clarification_question" key is only needed when route is "clarify" - ask the user specifically what they meant, referencing the closest-matching column names if it helps.
"""

_CODE_PROMPT = """You write short Python data-analysis scripts that run in a sandbox.

Dataset schema:
{schema}

The dataset is available as a pandas DataFrame: datasets["{dataset_name}"]
pandas is available as `pd`. DuckDB is available via `import duckdb` if useful.

Approach to use: {route}

Question: {question}

Rules:
- Assign the final answer to a variable named `result`. Keep it a plain
  number, string, list, or dict - not a matplotlib figure.
- Only compute the specific aggregated/filtered answer to the question - do
  not return raw row-level data.
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

Question: {question}

Return ONLY a JSON object with these keys:
- "chart_type": name whatever chart type is actually being requested (e.g. "histogram", "bar", "pie", "donut", "line", "area", "scatter", "box", "violin", or anything else like "radar" or "heatmap" if that's genuinely what was asked - do not force it into one of the common types if it isn't)
- "x": a column name for the x-axis or grouping/category, or null if not applicable
- "y": a numeric column name for the y-axis/value, or null if not needed (e.g. a histogram, or a count-based chart)
- "agg": one of "count", "sum", "mean", "median", or null
- "filters": a list of {{"column": ..., "op": "eq"|"ne"|"gt"|"gte"|"lt"|"lte"|"in", "value": ...}} objects, or an empty list
- "date_part": one of "year", "month", "quarter", or null - set this when x is a date column and the question asks for a breakdown/trend at a coarser granularity than individual exact dates (e.g. "per year", "by month", "each quarter", "yearly trend"). Only applies to bar/pie/donut/line/area charts. Leave null otherwise - do not set it just because x happens to be a date.
- "title": a short chart title

Guidance for the common types:
- histogram: distribution of ONE continuous numeric column (x). No y.
- bar/pie/donut: a value per category (x = categorical column). No y means count rows per category.
- line/area: a numeric column (y) over an ordered/time column (x), usually a date.
- scatter: relationship between two numeric columns (x and y).
- box/violin: distribution of a numeric column (y), optionally grouped by a categorical column (x).

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

The dataset is available as a pandas DataFrame: datasets["{dataset_name}"]
pandas is available as `pd`. plotly.express / plotly.graph_objects are available for plotting.
DuckDB is available via `import duckdb` - if you use it, pass the dataframe directly to
duckdb.sql("...", ...) or register it first; do not assume a SQL table already exists by the
dataset's name.

Question: {question}

Rules:
- Save the chart to f"{{ARTIFACTS_DIR}}/chart.html" using fig.write_html(path, include_plotlyjs="cdn")
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

The dataset is available as a pandas DataFrame: datasets["{dataset_name}"]
pandas is available as `pd`. DuckDB is available via `import duckdb` if useful.

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


def _node_plan(state: QAState) -> QAState:
    model = get_chat_model(temperature=0.0)
    prompt = _ROUTER_PROMPT.format(schema=state["schema_text"], question=state["question"])
    response = model.invoke(prompt)
    _track_usage(state, response)

    try:
        parsed = _extract_json(response.content)
        route = parsed.get("route")
        if route not in _ROUTES:
            route = "pandas"
    except (ValueError, json.JSONDecodeError):
        route, parsed = "pandas", {}

    state["route"] = route
    state["clarification"] = parsed.get("clarification_question") if route == "clarify" else None
    return state


def _node_generate_code(state: QAState) -> QAState:
    retry_context = ""
    if state.get("previous_error") and state.get("code"):
        retry_context = _RETRY_CONTEXT_TEMPLATE.format(previous_code=state["code"], error=state["previous_error"])

    model = get_chat_model(temperature=0.0)
    prompt = _CODE_PROMPT.format(
        schema=state["schema_text"],
        dataset_name=state["dataset_name"],
        route=state["route"],
        question=state["question"],
        retry_context=retry_context,
    )
    response = model.invoke(prompt)
    _track_usage(state, response)
    state["code"] = _extract_code(response.content)
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
        schema=state["schema_text"], question=state["question"], retry_context=retry_context
    )
    response = model.invoke(prompt)
    _track_usage(state, response)

    try:
        spec = _extract_json(response.content)
    except (ValueError, json.JSONDecodeError):
        spec = {"chart_type": None}

    state["chart_spec"] = spec
    state["attempt"] = state.get("attempt", 0) + 1
    return state


def _node_validate_chart(state: QAState) -> QAState:
    requested_type = normalize_chart_type((state.get("chart_spec") or {}).get("chart_type"))
    if requested_type not in SUPPORTED_CHART_TYPES:
        # Not one we structurally validate/render - fall back to freehand
        # generation for this specific request rather than rejecting it.
        state["chart_spec"] = None
        state["chart_notes"] = []
        state["needs_freehand_chart"] = True
        return state

    columns = get_column_profiles(state["dataset_id"])
    corrected, notes = validate_chart_spec(state["chart_spec"], columns)
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
        dataset_name=state["dataset_name"],
        question=state["question"],
        retry_context=retry_context,
    )
    response = model.invoke(prompt)
    _track_usage(state, response)
    state["code"] = _extract_code(response.content)
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
        dataset_name=state["dataset_name"],
        question=state["question"],
        retry_context=retry_context,
    )
    response = model.invoke(prompt)
    _track_usage(state, response)
    state["code"] = _extract_code(response.content)
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
        schema=state["schema_text"], other_datasets=other_datasets, question=state["question"],
        retry_context=retry_context,
    )
    response = model.invoke(prompt)
    _track_usage(state, response)

    try:
        spec = _extract_json(response.content)
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


def _node_execute(state: QAState) -> QAState:
    datasets = {state["dataset_name"]: Path(state["local_data_path"])}
    if state.get("right_dataset_name") and state.get("right_local_path"):
        datasets[state["right_dataset_name"]] = Path(state["right_local_path"])

    timeout = EDA_TIMEOUT_SECONDS if state["route"] == "eda" else EXECUTION_TIMEOUT_SECONDS
    res = run_python(state["code"], datasets, timeout_seconds=timeout)
    state["artifacts"] = res.artifacts
    state["execution_result"] = {
        "result": res.result,
        "error": res.error,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "timed_out": res.timed_out,
        "rejected": res.rejected,
        "oom_killed": res.oom_killed,
    }
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
        if "chart.html" not in (state.get("artifacts") or {}):
            state["verified"] = False
            state["verification_error"] = "The chart code ran but did not produce a chart.html artifact."
            return state
        state["verified"] = True
        state["verification_error"] = None
        return state

    if state["route"] == "eda":
        if "eda.html" not in (state.get("artifacts") or {}):
            state["verified"] = False
            state["verification_error"] = "The EDA code ran but did not produce a report."
            return state
        state["verified"] = True
        state["verification_error"] = None
        return state

    if state["route"] in ("transform", "join"):
        if "result.parquet" not in (state.get("artifacts") or {}):
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

    wants_number = any(kw in question for kw in _COUNT_KEYWORDS + _NUMERIC_KEYWORDS)
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
        return "finalize" if state["route"] in ("transform", "join") else "respond"
    if state["route"] == "eda":
        return "respond"  # deterministic code - retrying identically won't change the outcome
    return "retry" if state.get("attempt", 0) < MAX_ATTEMPTS else "respond"


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
    parquet_bytes = (state.get("artifacts") or {}).get("result.parquet")
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
    if state["route"] == "clarify":
        state["answer"] = state.get("clarification") or "Could you clarify what you mean?"
        return state

    if state["route"] == "chart" and not state.get("needs_freehand_chart") and state.get("execution_result") is None:
        # validate_chart couldn't salvage the structured spec at all - never reached execute.
        reason = " ".join(state.get("chart_notes") or []) or "I couldn't map that to a valid chart for this data."
        state["answer"] = reason
        state["final_error"] = reason
        return state

    if state["route"] == "join" and state.get("execution_result") is None:
        # resolve_join_dataset or validate_join failed outright - never reached execute.
        reason = state.get("verification_error") or "I couldn't set up that join."
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
        state["answer"] = (
            f"EDA report generated: {result.get('rows', '?')} rows, {result.get('columns', '?')} columns, "
            f"{result.get('duplicate_rows', '?')} duplicate row(s)."
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


def build_qa_graph():
    graph = StateGraph(QAState)
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

    graph.set_entry_point("plan")

    def _route_from_plan(s: QAState) -> str:
        route = s["route"]
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

    graph.add_conditional_edges("plan", _route_from_plan)
    graph.add_edge("generate_code", "execute")
    graph.add_edge("generate_transform", "execute")
    graph.add_edge("run_eda", "execute")

    graph.add_edge("generate_chart_spec", "validate_chart")
    graph.add_conditional_edges(
        "validate_chart",
        lambda s: "generate_freehand_chart"
        if s.get("needs_freehand_chart")
        else ("prepare_chart_code" if s.get("chart_spec") else "respond"),
    )
    graph.add_edge("prepare_chart_code", "execute")
    graph.add_edge("generate_freehand_chart", "execute")

    graph.add_edge("generate_join_spec", "resolve_join_dataset")
    graph.add_conditional_edges(
        "resolve_join_dataset", lambda s: "validate_join" if s.get("right_dataset_name") else "respond"
    )
    graph.add_conditional_edges(
        "validate_join", lambda s: "prepare_join_code" if s.get("join_spec") else "respond"
    )
    graph.add_edge("prepare_join_code", "execute")

    graph.add_edge("execute", "verify")
    graph.add_conditional_edges(
        "verify", _should_retry, {"retry": "prepare_retry", "finalize": "finalize_transform", "respond": "respond"}
    )
    graph.add_edge("finalize_transform", "respond")
    graph.add_conditional_edges(
        "prepare_retry",
        lambda s: "generate_freehand_chart"
        if s.get("needs_freehand_chart")
        else (
            "generate_chart_spec" if s.get("route") == "chart"
            else "generate_join_spec" if s.get("route") == "join"
            else "generate_transform" if s.get("route") == "transform"
            else "generate_code"
        ),
    )
    graph.add_edge("respond", END)

    return graph.compile()


def ask(dataset_id: str, question: str) -> QAResult:
    dataset_name, schema_text = build_schema_context(dataset_id)
    _, active_version = get_active_dataset(dataset_id)
    base_columns = [c.name for c in active_version.columns]
    base_row_count = active_version.row_count
    _, local_path = download_active_version(dataset_id)

    right_local_path: str | None = None
    try:
        graph = build_qa_graph()
        start = time.monotonic()
        final_state: QAState = graph.invoke(
            {
                "dataset_id": dataset_id,
                "dataset_name": dataset_name,
                "local_data_path": str(local_path),
                "question": question,
                "schema_text": schema_text,
                "base_columns": base_columns,
                "base_row_count": base_row_count,
                "attempt": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }
        )
        duration = time.monotonic() - start
        right_local_path = final_state.get("right_local_path")
    finally:
        shutil.rmtree(local_path.parent, ignore_errors=True)
        if right_local_path:
            shutil.rmtree(Path(right_local_path).parent, ignore_errors=True)

    exec_result = final_state.get("execution_result") or {}
    artifacts = final_state.get("artifacts") or {}
    artifact_bytes = artifacts.get("chart.html") or artifacts.get("eda.html")

    return QAResult(
        answer=final_state.get("answer", ""),
        route=_effective_route(
            final_state.get("route", ""), final_state.get("code"), final_state.get("needs_freehand_chart", False)
        ),
        code=final_state.get("code"),
        result=exec_result.get("result"),
        error=final_state.get("final_error"),
        prompt_tokens=final_state.get("prompt_tokens", 0),
        completion_tokens=final_state.get("completion_tokens", 0),
        duration_seconds=duration,
        attempts=final_state.get("attempt", 0),
        artifact_html=artifact_bytes.decode("utf-8") if artifact_bytes else None,
        diff=final_state.get("diff"),
        pending_parquet_key=final_state.get("pending_parquet_key"),
        join_spec=final_state.get("join_spec"),
    )
