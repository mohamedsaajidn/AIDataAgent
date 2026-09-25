"""Tool-calling orchestrator graph - an alternative to graphs.qa's fixed
router+per-route-subgraph design, built to answer a real architecture
question: a single classification into one of a closed set of routes
(sql/pandas/chart/eda/transform/join/clarify) is too rigid for anything that
doesn't fit that enum - a follow-up asking to interpret a prior result, a
"yes" confirming something the agent itself just offered, or plain
conversation (greetings, thanks) all got force-fit into "clarify" in
practice, which is what caused a real clarify-loop bug (see CLAUDE.md's
Q&A agent section / git history around that fix).

This graph keeps every safety-critical, deterministic piece of graphs.qa
completely intact - the sandbox execute/verify/retry loop, chart spec
validation with freehand fallback, PII-safe schema-only prompting, and the
two-phase propose/confirm transform flow (this graph can only ever PROPOSE
a transform/join, exactly like qa.py - committing stays a separate, human-
gated API call this graph's tools cannot reach). What changes is the
decision layer: instead of a closed-enum classifier, an LLM bound to a
fixed set of tools decides whether a question needs 0, 1, or more of them,
and answers directly (no tool call) for anything that doesn't need fresh
computation against the data - which is exactly what "explain that
result"/"yes"/"thanks" need and never had a home for before.

Every tool below is a thin wrapper around qa.run_route() - the exact same
node functions (generate_code, execute, verify, retry, chart validation,
etc.) the production graph already uses, replayed for a route this
orchestrator already decided on instead of qa.py's own router LLM call.
This is deliberate: every hardened behavior already fixed in qa.py
(ARTIFACTS_DIR/numpy/duckdb pre-imports, the chart freehand fallback, the
empty-result verify fix, etc.) applies here automatically with zero
duplicated logic and zero risk of the two graphs drifting apart.

Not wired into production yet - see AskRequest.use_orchestrator in
apps/api/src/api/routes/chat.py for how to opt into this per-request for
side-by-side comparison against the default qa.py graph before any cutover.
"""

import logging
import shutil
import time
from pathlib import Path
from typing import Literal, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph

from agent import memory
from agent.graphs.qa import (
    MAX_ATTEMPTS,
    QAResult,
    QAState,
    _build_memory_context,
    _effective_route,
    _node_extract_memory,
    _node_load_dataset,
    _node_load_memory,
    _node_resolve_target_dataset,
    _node_respond,
    _node_save_turn,
    run_route,
)
from agent.llm import get_chat_model, get_response_text

logger = logging.getLogger("agent.orchestrator")

# A tool round is one orchestrator turn that results in >=1 tool calls - not
# the same as qa.py's MAX_ATTEMPTS (that's retries within ONE tool's own
# execute/verify loop, already enforced inside run_route). This is the outer
# guardrail: how many times the orchestrator itself may go back and decide
# to call something again before being forced to answer with what it has.
# Matters more now that the model is a paid frontier model (GPT-4o-mini) -
# no free-tier rate-limit pressure capping this implicitly like before.
MAX_TOOL_ROUNDS = 3


class OrchestratorState(TypedDict, total=False):
    dataset_ids: list[str]
    dataset_id: str
    dataset_name: str
    local_data_path: str
    right_local_path: str | None
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
    messages: list[BaseMessage]
    tool_round_count: int
    tools_used: list[str]
    answer: str | None
    code: str | None
    result: object
    artifact_html: str | None
    diff: dict | None
    pending_parquet_key: str | None
    join_spec: dict | None
    final_error: str | None
    prompt_tokens: int
    completion_tokens: int


_ORCHESTRATOR_SYSTEM_PROMPT = """You are a data analysis assistant for the dataset "{dataset_name}".

Dataset schema:
{schema}

{memory_context}

You have tools to compute answers, make charts, run a full profiling report, and propose (never
apply - every tool here only ever produces a PREVIEW for the user to confirm, it never changes the
real data) changes to the dataset. Call exactly the tool(s) the question actually needs - more than
one in the same turn only if it genuinely needs both. query_data can already attach a chart
alongside a computed answer in one call (e.g. "train a model and show a scatter of results"), so
don't call make_chart separately for that - only call make_chart when no new computation is needed
first, just a plot of columns as they already exist.

For anything that ISN'T a request to compute/chart/profile/modify the dataset - explaining or
interpreting a result already visible in this conversation, a short reply like "yes"/"sure"
answering something you just asked or offered, or plain conversation (greetings, thanks,
goodbye) - respond directly in your own words, in plain text, without calling any tool. Only call a
tool when the question genuinely needs fresh work against the actual data.

NEVER answer from memory whether a previously-proposed change (a transform/join preview) has
since been applied, confirmed, rejected, or is reflected in the dataset right now. The user
confirms or rejects a preview through a separate action this conversation cannot see - "I made a
preview" earlier tells you nothing about whether it was later confirmed, and treating your own
past "here's a preview" message as still true is very likely to be wrong. If asked whether a
change has taken effect, whether outliers/duplicates/nulls are "handled now", or anything else
about the CURRENT state of the data, verify it with a tool (query_data to check the real row
count/values, or make_chart to show the real current data) - do not answer from what you recall
saying earlier.

A request that combines a data-changing instruction with a chart request (e.g. "drop the
outliers and plot the box plot") needs propose_transform called, even if you believe an
equivalent change may already be applied - propose_transform is always safe to call again, it
only ever creates another preview for the user to confirm, never applies anything on its own. If
you genuinely decide not to call it because the change is clearly already reflected, say so
explicitly in your answer - never silently do only the chart half of a two-part request without
mentioning the other half at all.

When a tool returns a specific computed number (a count, a p-value, a metric, anything precise),
quote it EXACTLY as the tool returned it in your final answer - never round it, approximate it, or
retype it from memory. You may add plain-language interpretation around it, but the number itself
must be copied, not paraphrased - a data agent that silently rounds or misquotes its own results is
worse than useless.
"""


@tool
def query_data(question: str) -> str:
    """Compute an answer from the dataset - counts, aggregations, filters, group-bys, statistics,
    correlations, hypothesis tests, model fitting/evaluation, or any other computation. Can also
    attach an interactive chart alongside the computed answer in the same call when the question
    asks for both (e.g. "give me the MSE and a scatter plot of actual vs predicted")."""
    raise NotImplementedError("dispatched via _TOOL_DISPATCH, never called directly")


@tool
def make_chart(question: str) -> str:
    """Create a chart/visualization of columns as they already exist in the dataset - histogram,
    bar, pie, donut, line, area, scatter, box, violin, or any other chart type - when NO new
    computation (like fitting a model) is needed first, just a plot of existing data."""
    raise NotImplementedError("dispatched via _TOOL_DISPATCH, never called directly")


@tool
def run_eda() -> str:
    """Generate a full exploratory profiling report for the whole dataset - use for "profile this
    data", "run EDA", "give me an overview/summary of the dataset", or per-column descriptions."""
    raise NotImplementedError("dispatched via _TOOL_DISPATCH, never called directly")


@tool
def propose_transform(question: str) -> str:
    """Propose a modification to the dataset - add/drop/rename a column, filter or dedupe rows,
    change a column's type, fill/drop missing values, remove outliers, bin/encode a column, or any
    other change. This ONLY EVER produces a preview for the user to confirm - it never applies
    anything, regardless of how the request is phrased."""
    raise NotImplementedError("dispatched via _TOOL_DISPATCH, never called directly")


@tool
def propose_join(question: str) -> str:
    """Propose combining this dataset with ANOTHER named dataset (merge/join/vlookup/"bring in
    columns from X"). Name the other dataset in the question. This ONLY EVER produces a preview for
    the user to confirm - it never applies anything."""
    raise NotImplementedError("dispatched via _TOOL_DISPATCH, never called directly")


_TOOLS = [query_data, make_chart, run_eda, propose_transform, propose_join]


def _build_sub_state(state: OrchestratorState, question: str) -> QAState:
    return {
        "dataset_id": state["dataset_id"],
        "dataset_name": state["dataset_name"],
        "local_data_path": state["local_data_path"],
        "schema_text": state["schema_text"],
        "base_columns": state.get("base_columns", []),
        "base_row_count": state.get("base_row_count", 0),
        "question": question,
        "recent_turns_text": state.get("recent_turns_text", ""),
        "long_term_facts_text": state.get("long_term_facts_text", ""),
        "attempt": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


def _extract_tool_result(planned_route: str, result_state: QAState) -> tuple[str, dict]:
    answer = result_state.get("answer") or "(no answer produced)"
    artifacts = result_state.get("artifacts") or {}
    artifact_bytes = artifacts.get("chart.html") or artifacts.get("eda.html")

    updates: dict = {
        "prompt_tokens": result_state.get("prompt_tokens", 0),
        "completion_tokens": result_state.get("completion_tokens", 0),
        "route": _effective_route(planned_route, result_state.get("code"), result_state.get("needs_freehand_chart", False)),
    }
    # Surfaced to QAResult.code/.result below - without this every message
    # persisted through the orchestrator had an empty generated_code, which
    # also meant PendingTransform.generated_code (and the version's
    # generated_code once confirmed) was empty for every transform/join
    # proposed this way, not just a frontend display gap.
    if result_state.get("code"):
        updates["code"] = result_state["code"]
    exec_result = result_state.get("execution_result")
    if exec_result is not None:
        updates["result"] = exec_result.get("result")
    if artifact_bytes:
        updates["artifact_html"] = artifact_bytes.decode("utf-8") if isinstance(artifact_bytes, bytes) else artifact_bytes
    if result_state.get("diff"):
        updates["diff"] = result_state["diff"]
        updates["pending_parquet_key"] = result_state.get("pending_parquet_key")
    if result_state.get("join_spec"):
        updates["join_spec"] = result_state["join_spec"]
    if result_state.get("right_local_path"):
        updates["right_local_path"] = result_state["right_local_path"]
    return answer, updates


def _tool_query_data(state: OrchestratorState, question: str) -> tuple[str, dict]:
    return _extract_tool_result("pandas", run_route("pandas", _build_sub_state(state, question)))


def _tool_make_chart(state: OrchestratorState, question: str) -> tuple[str, dict]:
    return _extract_tool_result("chart", run_route("chart", _build_sub_state(state, question)))


def _tool_run_eda(state: OrchestratorState) -> tuple[str, dict]:
    return _extract_tool_result("eda", run_route("eda", _build_sub_state(state, state["question"])))


def _tool_propose_transform(state: OrchestratorState, question: str) -> tuple[str, dict]:
    return _extract_tool_result("transform", run_route("transform", _build_sub_state(state, question)))


def _tool_propose_join(state: OrchestratorState, question: str) -> tuple[str, dict]:
    return _extract_tool_result("join", run_route("join", _build_sub_state(state, question)))


_TOOL_DISPATCH = {
    "query_data": _tool_query_data,
    "make_chart": _tool_make_chart,
    "run_eda": _tool_run_eda,
    "propose_transform": _tool_propose_transform,
    "propose_join": _tool_propose_join,
}


def _node_orchestrator(state: OrchestratorState) -> OrchestratorState:
    if not state.get("messages"):
        system_prompt = _ORCHESTRATOR_SYSTEM_PROMPT.format(
            dataset_name=state["dataset_name"],
            schema=state["schema_text"],
            memory_context=_build_memory_context(state),  # QAState-shaped keys match, safe to reuse
        )
        state["messages"] = [SystemMessage(content=system_prompt), HumanMessage(content=state["question"])]

    model = get_chat_model(temperature=0.0).bind_tools(_TOOLS)
    response = model.invoke(state["messages"])
    state["messages"] = [*state["messages"], response]

    tool_names = [c["name"] for c in (getattr(response, "tool_calls", None) or [])]
    logger.info("orchestrator turn: tool_calls=%s", tool_names or "(none, direct answer)")
    return state


def _route_after_orchestrator(state: OrchestratorState) -> Literal["run_tools", "finalize"]:
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None) or []
    if tool_calls and state.get("tool_round_count", 0) < MAX_TOOL_ROUNDS:
        return "run_tools"
    if tool_calls:
        logger.warning("hit MAX_TOOL_ROUNDS=%d with more tool calls pending - forcing a final answer", MAX_TOOL_ROUNDS)
    return "finalize"


def _node_run_tools(state: OrchestratorState) -> OrchestratorState:
    last = state["messages"][-1]
    tool_messages = []
    tools_used = list(state.get("tools_used", []))

    for call in last.tool_calls:
        name, args = call["name"], (call.get("args") or {})
        impl = _TOOL_DISPATCH.get(name)
        logger.info("tool_call: %s(%s)", name, args)
        if impl is None:
            tool_messages.append(ToolMessage(content=f"Unknown tool {name!r}.", tool_call_id=call["id"]))
            continue
        try:
            text, updates = impl(state, **args)
        except Exception as exc:  # a tool failing must not crash the whole turn
            logger.warning("tool %s raised: %s", name, exc)
            text, updates = f"That didn't work: {exc}", {}

        for key, value in updates.items():
            if key in ("prompt_tokens", "completion_tokens"):
                state[key] = state.get(key, 0) + value
            else:
                state[key] = value

        tools_used.append(name)
        tool_messages.append(ToolMessage(content=text, tool_call_id=call["id"]))

    state["messages"] = [*state["messages"], *tool_messages]
    state["tools_used"] = tools_used
    state["tool_round_count"] = state.get("tool_round_count", 0) + 1
    return state


def _node_finalize(state: OrchestratorState) -> OrchestratorState:
    state["answer"] = get_response_text(state["messages"][-1])
    if not state.get("route"):
        # No tool was ever called this turn - pure conversation/explanation,
        # exactly the class of turn the fixed router had no route for.
        state["route"] = "chat"
    return state


def _node_save_turn_orchestrator(state: OrchestratorState) -> OrchestratorState:
    # _node_save_turn only reads question/answer/route - same field names as
    # QAState, safe to reuse directly rather than reimplementing.
    return _node_save_turn(state)  # type: ignore[arg-type]


def build_orchestrator_graph():
    graph = StateGraph(OrchestratorState)
    graph.add_node("resolve_target_dataset", _node_resolve_target_dataset)
    graph.add_node("load_dataset", _node_load_dataset)
    graph.add_node("load_memory", _node_load_memory)
    graph.add_node("extract_memory", _node_extract_memory)
    graph.add_node("orchestrator", _node_orchestrator)
    graph.add_node("run_tools", _node_run_tools)
    graph.add_node("finalize", _node_finalize)
    graph.add_node("respond", _node_respond)  # reused as-is for the clarify/memory_ack short-circuits
    graph.add_node("save_turn", _node_save_turn_orchestrator)

    graph.set_entry_point("resolve_target_dataset")
    graph.add_conditional_edges(
        "resolve_target_dataset", lambda s: "respond" if s.get("route") == "clarify" else "load_dataset"
    )
    graph.add_edge("load_dataset", "load_memory")
    graph.add_edge("load_memory", "extract_memory")
    graph.add_conditional_edges(
        "extract_memory", lambda s: "respond" if s.get("route") == "memory_ack" else "orchestrator"
    )
    graph.add_conditional_edges("orchestrator", _route_after_orchestrator)
    graph.add_edge("run_tools", "orchestrator")
    graph.add_edge("finalize", "save_turn")
    graph.add_edge("respond", "save_turn")
    graph.add_edge("save_turn", END)

    return graph.compile()


def ask(dataset_ids: list[str], question: str, user_id: str, session_id: str) -> QAResult:
    graph = build_orchestrator_graph()
    logger.info(
        "ask() [orchestrator] start: user_id=%s session_id=%s dataset_ids=%s question=%r",
        user_id, session_id, dataset_ids, question,
    )
    start = time.monotonic()
    final_state: OrchestratorState = graph.invoke(
        {
            "dataset_ids": dataset_ids,
            "question": question,
            "user_id": user_id,
            "session_id": session_id,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "tool_round_count": 0,
            "tools_used": [],
        }
    )
    duration = time.monotonic() - start

    local_path = final_state.get("local_data_path")
    right_local_path = final_state.get("right_local_path")
    if local_path:
        shutil.rmtree(Path(local_path).parent, ignore_errors=True)
    if right_local_path:
        shutil.rmtree(Path(right_local_path).parent, ignore_errors=True)

    route = final_state.get("route") or "chat"
    final_error = final_state.get("final_error")
    log = logger.warning if final_error else logger.info
    log(
        "ask() [orchestrator] done: route=%s tools_used=%s tool_rounds=%s duration=%.1fs has_artifact=%s error=%r",
        route, final_state.get("tools_used"), final_state.get("tool_round_count", 0), duration,
        bool(final_state.get("artifact_html")), final_error,
    )

    return QAResult(
        answer=final_state.get("answer", ""),
        route=route,
        code=final_state.get("code"),
        result=final_state.get("result"),
        error=final_error,
        prompt_tokens=final_state.get("prompt_tokens", 0),
        completion_tokens=final_state.get("completion_tokens", 0),
        duration_seconds=duration,
        attempts=final_state.get("tool_round_count", 0),
        artifact_html=final_state.get("artifact_html"),
        diff=final_state.get("diff"),
        pending_parquet_key=final_state.get("pending_parquet_key"),
        join_spec=final_state.get("join_spec"),
        remembered_fact=final_state.get("remembered_fact"),
        resolved_dataset_id=final_state.get("dataset_id"),
    )
