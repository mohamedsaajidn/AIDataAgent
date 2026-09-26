import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    source_file_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.id", use_alter=True, name="fk_dataset_active_version"),
        nullable=True,
    )
    # Nullable: rows uploaded before this existed have no owner and are
    # treated as unlisted (not shown in anyone's default dataset list, but
    # still directly accessible by id - see "Dataset visibility" in
    # CLAUDE.md for what this does and doesn't enforce).
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    versions: Mapped[list["DatasetVersion"]] = relationship(
        back_populates="dataset",
        foreign_keys="DatasetVersion.dataset_id",
        cascade="all, delete-orphan",
    )


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version_number", name="uq_dataset_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # raw_key is only set on version 1 - later versions are derived, never re-uploaded.
    raw_key: Mapped[str | None] = mapped_column(String, nullable=True)
    parquet_key: Mapped[str] = mapped_column(String, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    # Lineage: how this version came to exist. "upload" for version 1; every
    # later version is derived from exactly one parent via a transform/join.
    source: Mapped[str] = mapped_column(String, nullable=False, default="upload")
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_versions.id", use_alter=True, name="fk_version_parent"), nullable=True
    )
    generated_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    dataset: Mapped["Dataset"] = relationship(back_populates="versions", foreign_keys=[dataset_id])
    columns: Mapped[list["Column"]] = relationship(back_populates="dataset_version", cascade="all, delete-orphan")


class Column(Base):
    __tablename__ = "columns"
    __table_args__ = (UniqueConstraint("dataset_version_id", "name", name="uq_version_column"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_versions.id"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    pandas_dtype: Mapped[str] = mapped_column(String, nullable=False)
    inferred_type: Mapped[str] = mapped_column(String, nullable=False)
    null_count: Mapped[int] = mapped_column(Integer, nullable=False)
    null_pct: Mapped[float] = mapped_column(Float, nullable=False)
    distinct_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cardinality_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    sample_values: Mapped[list] = mapped_column(JSON, nullable=False)
    is_pii: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pii_entities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    dataset_version: Mapped["DatasetVersion"] = relationship(back_populates="columns")


class User(Base):
    """No auth in this POC - `external_id` is just whatever stable identifier
    the caller supplies (an email, a frontend-generated id, etc.), resolved
    or auto-created on first use. This is an identity anchor, not a login
    system - it exists so long-term memory has something stable to key on
    across sessions.

    `role` (Phase 7 RBAC) is deliberately NOT something the caller can set
    via the request - it's stored here, on the row, and only ever changed
    by a direct DB update (see scripts/promote_user.py) or a future admin
    endpoint. A caller-supplied "I'm an editor" field would be trivially
    self-granted and defeat the entire point of the checklist item ("even
    by asking the agent directly"). New users default to "viewer" -
    least-privilege by default, not "editor unless told otherwise".
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="viewer")  # "viewer" | "editor"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable so pre-Phase-6 sessions (created before users existed) stay
    # valid - every session created from here on always sets it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )
    dataset_links: Mapped[list["ChatSessionDataset"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatSessionDataset.added_at"
    )


class ChatSessionDataset(Base):
    """A session can have several datasets attached (upload two files into one
    conversation, or attach another mid-chat) - this many-to-many table is
    what makes that possible instead of a session being pinned to one dataset
    forever. `added_at` lets the UI show upload order / "most recently added".
    """

    __tablename__ = "chat_session_datasets"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id"), primary_key=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["ChatSession"] = relationship(back_populates="dataset_links")
    dataset: Mapped["Dataset"] = relationship()


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chat_sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(String, nullable=False)

    # Assistant-message-only fields - always populated alongside an answer so
    # the UI can show "how did it get this" next to "what's the answer".
    route: Mapped[str | None] = mapped_column(String, nullable=True)  # "sql" | "pandas" | "clarify"
    generated_code: Mapped[str | None] = mapped_column(String, nullable=True)
    result_json: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Self-contained HTML (Plotly charts, ydata-profiling EDA reports) is
    # text, not binary, so it's stored and returned directly as a string
    # field - no separate binary-artifact endpoint needed for the checklist's
    # "retrievable from a past session". Named generically (not chart_html)
    # since EDA reports are HTML artifacts too, not just charts.
    artifact_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when this message proposed a transform/join awaiting confirmation.
    pending_transform_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pending_transforms.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["ChatSession"] = relationship(back_populates="messages")


class PendingTransform(Base):
    """A proposed transform/join that has been generated and executed against
    a copy, with the result sitting in S3 under a pending (not yet versioned)
    key - nothing here is visible to any Q&A query until confirmed. Confirm
    turns this into a real DatasetVersion; reject just discards it. Preview
    data (diff/row-col counts) is computed and stored at propose time, not
    recomputed at confirm time, so what the user approved is exactly what
    gets committed.
    """

    __tablename__ = "pending_transforms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    base_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_versions.id"), nullable=False
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)  # "transform" | "join"
    question: Mapped[str] = mapped_column(Text, nullable=False)
    generated_code: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # join parameters, when kind == "join"
    pending_parquet_key: Mapped[str] = mapped_column(String, nullable=False)
    diff_summary: Mapped[str] = mapped_column(Text, nullable=False)
    row_count_before: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count_after: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count_before: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count_after: Mapped[int] = mapped_column(Integer, nullable=False)
    columns_added: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    columns_removed: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")  # pending|confirmed|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
