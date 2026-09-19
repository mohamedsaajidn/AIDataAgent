"""Standalone check that LangGraph can reach an OpenRouter model end-to-end.

Run: uv run python scripts/smoke_llm.py
"""

from agent.graphs.echo import build_echo_graph


def main() -> None:
    graph = build_echo_graph()
    result = graph.invoke({"question": "Reply with exactly: pong", "answer": ""})
    print(result["answer"])


if __name__ == "__main__":
    main()
