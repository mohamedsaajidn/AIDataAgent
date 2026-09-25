from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.schema_context import DatasetNotFound
from api.services.chat import ask_question, get_session_history, list_sessions

router = APIRouter(prefix="/chat", tags=["chat"])


class AskRequest(BaseModel):
    # Datasets to attach to this session (in addition to whatever is already
    # attached, if session_id is given). Required non-empty when starting a
    # new session; can be empty when continuing one that already has
    # datasets attached.
    dataset_ids: list[str] = []
    question: str
    user_id: str
    session_id: str | None = None
    # agent.graphs.orchestrator (tool-calling architecture) is the default
    # Q&A engine as of Phase 7 - see CLAUDE.md's "Q&A agent architecture"
    # section for why. agent.graphs.qa (the original fixed router) is kept
    # as a documented, deliberately-not-deleted fallback: set this to true
    # to roll a request back to it, e.g. while debugging whether a given
    # issue is specific to the orchestrator. No UI toggle for this - it's an
    # operator/debugging escape hatch, not a user-facing choice.
    use_legacy_qa_graph: bool = False


@router.post("/ask")
def ask(request: AskRequest) -> dict:
    try:
        return ask_question(
            request.dataset_ids,
            request.question,
            request.user_id,
            request.session_id,
            use_legacy_qa_graph=request.use_legacy_qa_graph,
        )
    except DatasetNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/sessions")
def get_sessions(user_id: str) -> dict:
    return list_sessions(user_id)


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    try:
        return get_session_history(session_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
