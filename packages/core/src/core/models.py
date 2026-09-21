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


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )


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
    # Plotly HTML is self-contained text (not binary), so it's stored and
    # returned directly as a string field - no separate binary-artifact
    # endpoint needed for the checklist's "retrievable from a past session".
    chart_html: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
