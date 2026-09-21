import json
import uuid

from sqlalchemy.orm import selectinload

from agent.graphs.qa import ask as ask_agent
from agent.schema_context import get_active_dataset
from core.db import get_session
from core.models import ChatMessage, ChatSession, PendingTransform


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

    _, base_version = get_active_dataset(dataset_id)
    result = ask_agent(dataset_id, question)
    latency_ms = int(result.duration_seconds * 1000)

    pending_transform_id = None
    if result.route in ("transform", "join") and result.diff and result.pending_parquet_key:
        pending_transform_id = uuid.uuid4()
        with get_session() as session:
            session.add(
                PendingTransform(
                    id=pending_transform_id,
                    dataset_id=uuid.UUID(dataset_id),
                    base_version_id=base_version.id,
                    session_id=session_id_value,
                    kind=result.route,
                    question=question,
                    generated_code=result.code or "",
                    spec=result.join_spec if result.route == "join" else None,
                    pending_parquet_key=result.pending_parquet_key,
                    diff_summary=result.diff["summary"],
                    row_count_before=result.diff["row_count_before"],
                    row_count_after=result.diff["row_count_after"],
                    column_count_before=result.diff["column_count_before"],
                    column_count_after=result.diff["column_count_after"],
                    columns_added=result.diff["columns_added"],
                    columns_removed=result.diff["columns_removed"],
                )
            )

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
                artifact_html=result.artifact_html,
                pending_transform_id=pending_transform_id,
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
        "artifact_html": result.artifact_html,
        "diff": result.diff,
        "pending_transform_id": str(pending_transform_id) if pending_transform_id else None,
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
                    "artifact_html": m.artifact_html,
                    "pending_transform_id": str(m.pending_transform_id) if m.pending_transform_id else None,
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
