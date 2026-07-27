"""Secure intake service for the short-pause robot."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bitrix.security import event_token_fingerprint, verify_application_token
from app.jobs.repository import AutomationJobRepository, JobCreationResult
from app.models.job import JobStatus
from app.models.portal import BitrixPortal, PortalStatus
from app.robots.payload import RobotExecutionPayload
from app.robots.short_pause import SHORT_PAUSE_CODE
from app.security.encryption import EncryptionService


class RobotCredentialError(Exception):
    """Portal callback credentials cannot be confirmed."""


class ShortPauseJobService:
    def __init__(self, session: AsyncSession, encryption: EncryptionService) -> None:
        self._session, self._encryption = session, encryption
        self._repository = AutomationJobRepository(session)

    async def create(self, callback: RobotExecutionPayload) -> JobCreationResult:
        async with self._session.begin():
            portal = await self._session.scalar(
                select(BitrixPortal).where(BitrixPortal.member_id == callback.member_id)
            )
            if (
                portal is None
                or portal.status != PortalStatus.ACTIVE.value
                or portal.application_token_hash is None
                or not verify_application_token(
                    callback.application_token, portal.application_token_hash
                )
            ):
                raise RobotCredentialError("Robot credential is invalid")
            scheduled_at = datetime.now(UTC)
            payload: dict[str, Any] = {
                "requested_delay_seconds": callback.delay_seconds,
                "comment": callback.comment,
                "scheduled_at": scheduled_at.isoformat(),
            }
            if callback.document_id is not None:
                payload["document_id"] = callback.document_id
            if callback.document_type is not None:
                payload["document_type"] = callback.document_type
            return await self._repository.create_idempotent(
                {
                    "portal_id": portal.id,
                    "robot_code": SHORT_PAUSE_CODE,
                    "event_token_encrypted": self._encryption.encrypt(callback.event_token),
                    "event_token_hash": event_token_fingerprint(callback.event_token),
                    "payload": payload,
                    "return_values": {
                        "status": JobStatus.PENDING.value,
                        "requested_delay_seconds": callback.delay_seconds,
                    },
                    "run_at": scheduled_at + timedelta(seconds=callback.delay_seconds),
                    "status": JobStatus.PENDING.value,
                    "attempts": 0,
                    "max_attempts": 10,
                }
            )
