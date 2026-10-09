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


def _pending_transform_diff(p: PendingTransform) -> dict:
    return {
        "columns_added": p.columns_added,
        "columns_removed": p.columns_removed,
        "row_count_before": p.row_count_before,
        "row_count_after": p.row_count_after,
        "column_count_before": p.column_count_before,
        "column_count_after": p.column_count_after,
        "summary": p.diff_summary,
    }


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

    assistant_message_id = uuid.uuid4()
    with get_session() as session:
        session.add(
            ChatMessage(
                id=assistant_message_id,
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
        "message_id": str(assistant_message_id),
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
        # The artifact (a chart/EDA report - several MB, sometimes 10+MB now
        # that a multi-chart turn combines more than one - see
        # orchestrator._combine_chart_htmls) is fetched separately via
        # GET /chat/messages/{message_id}/artifact instead of inlined here.
        # See get_session_history's identical change for why: inlining it
        # in every response referencing this message is what made a
        # chart-heavy session balloon to 80+MB per history load - confirmed
        # live (a real 40-message session measured at 84MB), and the
        # likely cause of intermittent request failures on chart turns
        # specifically (both the size and the extra agent time a multi-
        # chart turn now genuinely takes).
        "has_artifact": bool(result.artifact_html),
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

        # A message's diff/status only lives on PendingTransform (see its
        # model docstring) - without re-attaching it here, reloading a
        # session (a hard refresh, or just revisiting it later) silently
        # dropped the Confirm/Reject card for a still-pending transform,
        # with no other way in the UI to act on it. Batched into one query
        # rather than N+1 per message.
        pending_ids = {m.pending_transform_id for m in chat_session.messages if m.pending_transform_id}
        pending_by_id: dict[uuid.UUID, PendingTransform] = {}
        if pending_ids:
            pendings = session.scalars(
                select(PendingTransform).where(PendingTransform.id.in_(pending_ids))
            ).all()
            pending_by_id = {p.id: p for p in pendings}

        messages = []
        for m in chat_session.messages:
            pending = pending_by_id.get(m.pending_transform_id) if m.pending_transform_id else None
            messages.append(
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
                    "id": str(m.id),
                    "has_artifact": bool(m.artifact_html),
                    "pending_transform_id": str(m.pending_transform_id) if m.pending_transform_id else None,
                    "diff": _pending_transform_diff(pending) if pending else None,
                    "pending_transform_status": pending.status if pending else None,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
            )

        return {
            "session_id": str(chat_session.id),
            "dataset_ids": [str(link.dataset_id) for link in chat_session.dataset_links],
            "user_id": str(chat_session.user_id) if chat_session.user_id else None,
            "messages": messages,
        }


class MessageArtifactNotFound(Exception):
    pass


def get_message_artifact(message_id: str) -> str:
    """Fetched on demand (GET /chat/messages/{id}/artifact) rather than
    inlined into ask_question's or get_session_history's response - see the
    "has_artifact" comment on both for why: a chart/EDA artifact is
    several MB (occasionally 10+MB for a multi-chart turn), and sending
    every historical message's artifact on every session load doesn't
    scale - confirmed live at 84MB for one real 40-message session."""
    with get_session() as session:
        message = session.get(ChatMessage, uuid.UUID(message_id))
        if message is None or not message.artifact_html:
            raise MessageArtifactNotFound(f"No artifact found for message {message_id}")
        return message.artifact_html


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
