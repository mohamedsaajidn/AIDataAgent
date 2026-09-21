import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph

from agent.dataset_files import download_active_version
from agent.llm import get_chat_model
from agent.schema_context import build_schema_context, get_column_profiles
from sandbox.executor import run_python
from tools.chart_render import build_chart_code, default_title
from tools.chart_spec import SUPPORTED_CHART_TYPES, normalize_chart_type, validate_chart_spec

MAX_ATTEMPTS = 3
EXECUTION_TIMEOUT_SECONDS = 20


class QAState(TypedDict, total=False):
    dataset_id: str
    dataset_name: str
    local_data_path: str
    question: str
    schema_text: str
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
    chart_html: str | None = None


_ROUTER_PROMPT = """You are routing a data-analysis question to the right approach.

Dataset schema:
{schema}

Question: {question}

Decide one of:
- "sql": the question is a straightforward aggregate/filter/count/group-by that DuckDB SQL handles well
- "pandas": the question needs row-wise logic, text analysis, or something SQL is awkward for
- "chart": the question explicitly or implicitly asks for a visualization (chart, plot, graph, histogram, bar chart, line chart, box plot, "show me", "visualize")
- "clarify": the question is too ambiguous to answer confidently against this schema (e.g. it references something that isn't clearly identifiable as any column)

Respond with ONLY a JSON object: {{"route": "sql"|"pandas"|"chart"|"clarify", "clarification_question": "..."}}
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

_COUNT_KEYWORDS = ("how many", "count", "number of")
_NUMERIC_KEYWORDS = ("average", "mean", "sum", "total", "median")


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
        if route not in ("sql", "pandas", "chart", "clarify"):
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


def _node_execute(state: QAState) -> QAState:
    res = run_python(
        state["code"],
        {state["dataset_name"]: Path(state["local_data_path"])},
        timeout_seconds=EXECUTION_TIMEOUT_SECONDS,
    )
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


def _should_retry(state: QAState) -> Literal["retry", "respond"]:
    if state.get("verified"):
        return "respond"
    return "retry" if state.get("attempt", 0) < MAX_ATTEMPTS else "respond"


def _node_prepare_retry(state: QAState) -> QAState:
    state["previous_error"] = state.get("verification_error")
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

    if not state.get("verified"):
        subject = "chart" if state["route"] == "chart" else "answer"
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

    # Freehand chart, or a plain sql/pandas answer - both already produced a
    # self-describing `result` (the freehand prompt requires stating any
    # assumptions made, so there's nothing extra to add here).
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
    if planned_route in ("clarify", "chart") or not code:
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
    graph.add_node("execute", _node_execute)
    graph.add_node("verify", _node_verify)
    graph.add_node("prepare_retry", _node_prepare_retry)
    graph.add_node("respond", _node_respond)

    graph.set_entry_point("plan")
    graph.add_conditional_edges(
        "plan",
        lambda s: "respond"
        if s["route"] == "clarify"
        else ("generate_chart_spec" if s["route"] == "chart" else "generate_code"),
    )
    graph.add_edge("generate_code", "execute")
    graph.add_edge("generate_chart_spec", "validate_chart")
    graph.add_conditional_edges(
        "validate_chart",
        lambda s: "generate_freehand_chart"
        if s.get("needs_freehand_chart")
        else ("prepare_chart_code" if s.get("chart_spec") else "respond"),
    )
    graph.add_edge("prepare_chart_code", "execute")
    graph.add_edge("generate_freehand_chart", "execute")
    graph.add_edge("execute", "verify")
    graph.add_conditional_edges("verify", _should_retry, {"retry": "prepare_retry", "respond": "respond"})
    graph.add_conditional_edges(
        "prepare_retry",
        lambda s: "generate_freehand_chart"
        if s.get("needs_freehand_chart")
        else ("generate_chart_spec" if s.get("route") == "chart" else "generate_code"),
    )
    graph.add_edge("respond", END)

    return graph.compile()


def ask(dataset_id: str, question: str) -> QAResult:
    dataset_name, schema_text = build_schema_context(dataset_id)
    _, local_path = download_active_version(dataset_id)

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
                "attempt": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }
        )
        duration = time.monotonic() - start
    finally:
        shutil.rmtree(local_path.parent, ignore_errors=True)

    exec_result = final_state.get("execution_result") or {}
    chart_html_bytes = (final_state.get("artifacts") or {}).get("chart.html")
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
        chart_html=chart_html_bytes.decode("utf-8") if chart_html_bytes else None,
    )
