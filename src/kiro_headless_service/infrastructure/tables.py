from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("owner", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    closed: Mapped[bool] = mapped_column(default=False)
    active_job: Mapped[str | None] = mapped_column(String(36))
    next_seq: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("owner", "operation", "idempotency_key"),
        Index("ix_jobs_queue", "state", "available_at", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    operation: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(128))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(16), default="QUEUED")
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(String(64))
    worker: Mapped[str | None] = mapped_column(String(128))
    token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    attempts: Mapped[int] = mapped_column(default=0)
    event_count: Mapped[int] = mapped_column(default=0)
    consumer_max_running: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("session_id", "seq"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[Any] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16))


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    url: Mapped[str] = mapped_column(String(2048))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(default=0)
    delivered: Mapped[bool] = mapped_column(default=False)
    exhausted: Mapped[bool] = mapped_column(default=False)
    token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Heartbeat(Base):
    __tablename__ = "heartbeats"
    worker: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
