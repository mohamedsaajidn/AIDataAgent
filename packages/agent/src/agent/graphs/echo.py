from typing import TypedDict

from langgraph.graph import END, StateGraph

from agent.llm import get_chat_model


class EchoState(TypedDict):
    question: str
    answer: str


def _respond(state: EchoState) -> EchoState:
    model = get_chat_model()
    response = model.invoke(state["question"])
    return {"question": state["question"], "answer": response.content}


def build_echo_graph():
    graph = StateGraph(EchoState)
    graph.add_node("respond", _respond)
    graph.set_entry_point("respond")
    graph.add_edge("respond", END)
    return graph.compile()
