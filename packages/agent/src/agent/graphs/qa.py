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
from agent.schema_context import build_schema_context
from sandbox.executor import run_python

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


_ROUTER_PROMPT = """You are routing a data-analysis question to the right approach.

Dataset schema:
{schema}

Question: {question}

Decide one of:
- "sql": the question is a straightforward aggregate/filter/count/group-by that DuckDB SQL handles well
- "pandas": the question needs row-wise logic, text analysis, or something SQL is awkward for
- "clarify": the question is too ambiguous to answer confidently against this schema (e.g. it references something that isn't clearly identifiable as any column)

Respond with ONLY a JSON object: {{"route": "sql"|"pandas"|"clarify", "clarification_question": "..."}}
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
        if route not in ("sql", "pandas", "clarify"):
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


def _node_execute(state: QAState) -> QAState:
    res = run_python(
        state["code"],
        {state["dataset_name"]: Path(state["local_data_path"])},
        timeout_seconds=EXECUTION_TIMEOUT_SECONDS,
    )
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

    if not state.get("verified"):
        state["answer"] = (
            "I tried a few times but couldn't compute a reliable answer to that question. "
            f"Last issue: {state.get('verification_error')}"
        )
        state["final_error"] = state.get("verification_error")
        return state

    state["answer"] = _format_result(state["execution_result"]["result"])
    return state


def _effective_route(planned_route: str, code: str | None) -> str:
    """`plan` picks sql/pandas before any code exists - it's a hint fed into
    the generate_code prompt, never enforced. The model is free to ignore it
    (e.g. use plain pandas for a filter the router guessed was SQL-shaped).
    Report what the accepted code actually does, not the pre-code guess -
    the "route" field should describe reality, not planning intent.
    """
    if planned_route == "clarify" or not code:
        return planned_route
    return "sql" if "duckdb" in code else "pandas"


def build_qa_graph():
    graph = StateGraph(QAState)
    graph.add_node("plan", _node_plan)
    graph.add_node("generate_code", _node_generate_code)
    graph.add_node("execute", _node_execute)
    graph.add_node("verify", _node_verify)
    graph.add_node("prepare_retry", _node_prepare_retry)
    graph.add_node("respond", _node_respond)

    graph.set_entry_point("plan")
    graph.add_conditional_edges("plan", lambda s: "respond" if s["route"] == "clarify" else "generate_code")
    graph.add_edge("generate_code", "execute")
    graph.add_edge("execute", "verify")
    graph.add_conditional_edges("verify", _should_retry, {"retry": "prepare_retry", "respond": "respond"})
    graph.add_edge("prepare_retry", "generate_code")
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
    return QAResult(
        answer=final_state.get("answer", ""),
        route=_effective_route(final_state.get("route", ""), final_state.get("code")),
        code=final_state.get("code"),
        result=exec_result.get("result"),
        error=final_state.get("final_error"),
        prompt_tokens=final_state.get("prompt_tokens", 0),
        completion_tokens=final_state.get("completion_tokens", 0),
        duration_seconds=duration,
        attempts=final_state.get("attempt", 0),
    )
