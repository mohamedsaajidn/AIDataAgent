from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.schema_context import DatasetNotFound
from api.services.chat import ask_question, get_session_history

router = APIRouter(prefix="/chat", tags=["chat"])


class AskRequest(BaseModel):
    dataset_id: str
    question: str
    session_id: str | None = None


@router.post("/ask")
def ask(request: AskRequest) -> dict:
    try:
        return ask_question(request.dataset_id, request.question, request.session_id)
    except DatasetNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    try:
        return get_session_history(session_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
