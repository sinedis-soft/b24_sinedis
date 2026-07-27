"""Persisted automation job model."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.portal import BitrixPortal


class JobStatus(StrEnum):
    """Lifecycle states accepted for an automation job."""

    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AutomationJob(TimestampMixin, Base):
    """A durable deferred callback for one Bitrix24 process instance."""

    __tablename__ = "automation_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'completed', 'failed', "
            "'expired', 'cancelled')",
            name="job_status",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("attempts <= max_attempts", name="attempts_within_limit"),
        UniqueConstraint(
            "portal_id", "event_token_hash", name="uq_automation_jobs_portal_id_event_token_hash"
        ),
        Index("ix_automation_jobs_portal_id", "portal_id"),
        Index(
            "ix_automation_jobs_due",
            "run_at",
            postgresql_where=text("status IN ('pending', 'retry')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bitrix_portals.id"), nullable=False
    )
    robot_code: Mapped[str] = mapped_column(String(100), nullable=False)
    event_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    event_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    return_values: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=JobStatus.PENDING.value,
        server_default=text("'pending'"),
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default=text("10")
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    portal: Mapped[BitrixPortal] = relationship(back_populates="jobs")
