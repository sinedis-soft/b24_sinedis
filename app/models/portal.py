"""Persisted Bitrix24 portal model."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.job import AutomationJob


class PortalStatus(StrEnum):
    """Lifecycle states accepted for a portal."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    AUTH_ERROR = "auth_error"


class BitrixPortal(TimestampMixin, Base):
    """A Bitrix24 installation and its encrypted authentication material."""

    __tablename__ = "bitrix_portals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'inactive', 'auth_error')",
            name="portal_status",
        ),
        Index("ix_bitrix_portals_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    client_endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    server_endpoint: Mapped[str | None] = mapped_column(String(500))
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    application_token_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=PortalStatus.ACTIVE.value,
        server_default=text("'active'"),
    )

    jobs: Mapped[list[AutomationJob]] = relationship(back_populates="portal")
