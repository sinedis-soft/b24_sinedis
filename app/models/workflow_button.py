"""Portal-scoped button configuration and durable workflow launch audit."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class WorkflowButton(TimestampMixin, Base):
    __tablename__ = "workflow_buttons"
    __table_args__ = (UniqueConstraint("portal_id", "field_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bitrix_portals.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class WorkflowLaunch(TimestampMixin, Base):
    __tablename__ = "workflow_launches"
    __table_args__ = (
        UniqueConstraint("portal_id", "request_id"),
        Index("ix_workflow_launches_button_deal", "button_id", "deal_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bitrix_portals.id"), nullable=False)
    button_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_buttons.id"), nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    deal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    template_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(String(100))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[int | None] = mapped_column(Integer)
