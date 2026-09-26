import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from agent.graphs.orchestrator import ask as ask_orchestrator
from agent.graphs.qa import ask as ask_legacy_qa
from agent.schema_context import get_active_dataset
from agent.tracing import flush as flush_traces
from api.services.users import get_or_create_user
from core.db import get_session
from core.models import ChatMessage, ChatSession, ChatSessionDataset, PendingTransform


def ask_question(
    dataset_ids: list[str],
    question: str,
    user_id: str,
    session_id: str | None = None,
    use_legacy_qa_graph: bool = False,
) -> dict:
    if not session_id and not dataset_ids:
        raise ValueError("Starting a new session requires at least one dataset_id")

    user = get_or_create_user(user_id)

    with get_session() as session:
        if session_id:
            chat_session = session.get(
                ChatSession, uuid.UUID(session_id), options=[selectinload(ChatSession.dataset_links)]
            )
            if chat_session is None:
                raise ValueError(f"No chat session found with id {session_id}")
            if chat_session.user_id is not None and chat_session.user_id != user.id:
                raise ValueError(f"Session {session_id} belongs to a different user")
            existing_ids = {str(link.dataset_id) for link in chat_session.dataset_links}
        else:
            chat_session = ChatSession(id=uuid.uuid4(), user_id=user.id)
            session.add(chat_session)
            session.flush()
            existing_ids = set()

        # New dataset_ids not already attached get attached now - this is
        # what lets "add this file to my chat" work mid-conversation, not
        # just at session creation.
        for did in dataset_ids:
            if did not in existing_ids:
                session.add(ChatSessionDataset(session_id=chat_session.id, dataset_id=uuid.UUID(did)))

        session.add(ChatMessage(id=uuid.uuid4(), session_id=chat_session.id, role="user", content=question))
        session_id_value = chat_session.id
        all_dataset_ids = list(existing_ids | set(dataset_ids))

    agent_fn = ask_legacy_qa if use_legacy_qa_graph else ask_orchestrator
    result = agent_fn(all_dataset_ids, question, user_id=str(user.id), session_id=str(session_id_value))
    latency_ms = int(result.duration_seconds * 1000)
    # See agent.tracing.flush()'s docstring - without this, some fraction of
    # requests' trace spans (notably the root wrapping span) never reached
    # Phoenix at all, verified live. Every request path through here, not
    # just the happy path, so this can't get silently skipped.
    flush_traces()

    pending_transform_id = None
    if result.route in ("transform", "join") and result.diff and result.pending_parquet_key:
        _, base_version = get_active_dataset(result.resolved_dataset_id)
        pending_transform_id = uuid.uuid4()
        with get_session() as session:
            session.add(
                PendingTransform(
                    id=pending_transform_id,
                    dataset_id=uuid.UUID(result.resolved_dataset_id),
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
        "user_id": str(user.id),
        "dataset_ids": all_dataset_ids,
        "resolved_dataset_id": result.resolved_dataset_id,
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
        "remembered_fact": result.remembered_fact,
    }


def get_session_history(session_id: str) -> dict:
    with get_session() as session:
        chat_session = session.get(
            ChatSession,
            uuid.UUID(session_id),
            options=[selectinload(ChatSession.messages), selectinload(ChatSession.dataset_links)],
        )
        if chat_session is None:
            raise ValueError(f"No chat session found with id {session_id}")
        return {
            "session_id": str(chat_session.id),
            "dataset_ids": [str(link.dataset_id) for link in chat_session.dataset_links],
            "user_id": str(chat_session.user_id) if chat_session.user_id else None,
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


def list_sessions(user_id: str) -> dict:
    user = get_or_create_user(user_id)
    with get_session() as session:
        sessions = session.scalars(
            select(ChatSession)
            .where(ChatSession.user_id == user.id)
            .options(selectinload(ChatSession.dataset_links), selectinload(ChatSession.messages))
            .order_by(ChatSession.created_at.desc())
        ).all()
        return {
            "sessions": [
                {
                    "session_id": str(s.id),
                    "dataset_ids": [str(link.dataset_id) for link in s.dataset_links],
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "message_count": len(s.messages),
                    "last_question": next((m.content for m in reversed(s.messages) if m.role == "user"), None),
                }
                for s in sessions
            ]
        }


def _jsonable(value: object) -> object:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)
