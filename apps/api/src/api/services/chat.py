import json
import uuid

from sqlalchemy.orm import selectinload

from agent.graphs.qa import ask as ask_agent
from core.db import get_session
from core.models import ChatMessage, ChatSession


def ask_question(dataset_id: str, question: str, session_id: str | None = None) -> dict:
    with get_session() as session:
        if session_id:
            chat_session = session.get(ChatSession, uuid.UUID(session_id))
            if chat_session is None:
                raise ValueError(f"No chat session found with id {session_id}")
        else:
            chat_session = ChatSession(id=uuid.uuid4(), dataset_id=uuid.UUID(dataset_id))
            session.add(chat_session)
            session.flush()

        session.add(ChatMessage(id=uuid.uuid4(), session_id=chat_session.id, role="user", content=question))
        session_id_value = chat_session.id

    result = ask_agent(dataset_id, question)
    latency_ms = int(result.duration_seconds * 1000)

    with get_session() as session:
        session.add(
            ChatMessage(
                id=uuid.uuid4(),
                session_id=session_id_value,
                role="assistant",
                content=result.answer,
                route=result.route,
                generated_code=result.code,
                result_json=_jsonable(result.result),
                error=result.error,
                latency_ms=latency_ms,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                attempts=result.attempts,
            )
        )

    return {
        "session_id": str(session_id_value),
        "answer": result.answer,
        "route": result.route,
        "code": result.code,
        "result": result.result,
        "error": result.error,
        "latency_ms": latency_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "attempts": result.attempts,
    }


def get_session_history(session_id: str) -> dict:
    with get_session() as session:
        chat_session = session.get(
            ChatSession, uuid.UUID(session_id), options=[selectinload(ChatSession.messages)]
        )
        if chat_session is None:
            raise ValueError(f"No chat session found with id {session_id}")
        return {
            "session_id": str(chat_session.id),
            "dataset_id": str(chat_session.dataset_id),
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "route": m.route,
                    "code": m.generated_code,
                    "result": m.result_json,
                    "error": m.error,
                    "latency_ms": m.latency_ms,
                    "prompt_tokens": m.prompt_tokens,
                    "completion_tokens": m.completion_tokens,
                    "attempts": m.attempts,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in chat_session.messages
            ],
        }


def _jsonable(value: object) -> object:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)
