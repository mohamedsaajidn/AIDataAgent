"""Phase 7 observability wiring - OpenTelemetry traces exported to a
self-hosted Arize Phoenix instance (see CLAUDE.md's "Guardrails" section for
why Phoenix over Langfuse: this machine can't run Langfuse's 16GB-minimum
stack). `setup_tracing()` is called once, from apps.api.main's lifespan,
before any graph runs - never instantiate a tracer/instrumentor anywhere
else, and never call this more than once per process.

LangChainInstrumentor auto-instruments every `model.invoke(...)` call
(plan/generate_code/generate_chart_spec/etc. all go through
agent.llm.get_chat_model(), a LangChain chat model) AND every LangGraph node
execution as its own child span, since a compiled StateGraph is itself a
LangChain Runnable traced through the same callback manager - so "plan",
"verify", and "respond" all already get a span for free. The one thing that
genuinely doesn't: `sandbox.executor.run_python` is a raw subprocess call
inside the "execute" node, not a LangChain/LangGraph construct, so it gets
an explicit child span here with the fields that actually matter for
debugging a run (attempt, timeout, timed_out/oom_killed/rejected flags) -
without this, the "execute" span would be an opaque black box in a trace
that's otherwise fully annotated.
"""

import logging

from opentelemetry import trace

from core.settings import get_settings

logger = logging.getLogger("agent.tracing")

_tracer = None


def setup_tracing() -> None:
    global _tracer
    settings = get_settings()
    if not settings.phoenix_enabled:
        logger.info("Phoenix observability disabled (PHOENIX_ENABLED=false) - no traces will be exported.")
        return
    if _tracer is not None:
        return  # already set up - guards a double-call (e.g. uvicorn --reload re-importing main)

    from openinference.instrumentation.langchain import LangChainInstrumentor
    from phoenix.otel import register

    tracer_provider = register(
        project_name=settings.phoenix_project_name,
        endpoint=settings.phoenix_collector_endpoint,
        auto_instrument=False,
        batch=True,
    )
    LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
    _tracer = tracer_provider.get_tracer("agent.sandbox")
    logger.info(
        "Phoenix observability enabled - exporting to %s (project=%s)",
        settings.phoenix_collector_endpoint,
        settings.phoenix_project_name,
    )


def get_tracer():
    """None when Phoenix is disabled or setup_tracing() hasn't run yet -
    callers (agent.graphs.qa._node_execute) must treat a manual span as
    optional instrumentation, never load-bearing for the request to work.
    """
    return _tracer


def flush() -> None:
    """Forces the batch span processor to export immediately instead of
    waiting for its periodic interval - called once per request (see
    apps.api.services.chat.ask_question, right after agent_fn() returns) so
    every span for that question is guaranteed to reach Phoenix before the
    HTTP response returns, including the ask()-level wrapping span, which
    ends last since it wraps the whole graph.invoke() call.

    Not just a "make traces show up sooner" convenience - verified live
    that without an explicit flush, some fraction of requests' root
    wrapping span (and even qa._node_execute's sandbox.execute span) never
    arrived at Phoenix at all, even 20+ seconds past the default 5s batch
    export interval. Root cause not fully isolated (suspected: an export
    race specific to manually-created spans under this SDK version, since
    LangChain's own auto-instrumented spans were never observed missing)
    - forcing a flush after every request sidesteps it rather than
    depending on background batching timing that's already been shown to
    drop spans in this setup.
    """
    if _tracer is None:
        return
    trace.get_tracer_provider().force_flush()
