"""Process claimed automation jobs and resume Bitrix24 subscriptions."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bitrix.exceptions import (
    BitrixAuthenticationError,
    BitrixConfigurationError,
    BitrixInvalidResponseError,
    BitrixOAuthConfigurationError,
    BitrixOAuthRefreshRejectedError,
    BitrixOAuthTemporaryError,
    BitrixPermanentError,
    BitrixPermissionError,
    BitrixRateLimitError,
    BitrixTemporaryError,
    BitrixTransportError,
)
from app.bitrix.oauth import PortalOAuthService
from app.config import Settings, get_settings
from app.jobs.repository import AutomationJobRepository
from app.models.job import AutomationJob
from app.models.portal import BitrixPortal, PortalStatus
from app.robots.short_pause import SHORT_PAUSE_CODE
from app.security.encryption import EncryptionError, EncryptionService

Clock = Callable[[], datetime]


def retry_delay_seconds(
    *, attempts: int, base: float, maximum: float, jitter: float, random_value: float
) -> float:
    """Calculate bounded exponential delay with injectable non-cryptographic jitter."""
    exponential = min(maximum, base * (2 ** max(0, attempts - 1)))
    return min(maximum, exponential + max(0.0, min(1.0, random_value)) * jitter)


def retry_at(
    *,
    now: datetime,
    attempts: int,
    settings: Settings,
    random_value: float,
    retry_after_seconds: float | None = None,
    operating_reset_at: datetime | None = None,
) -> datetime:
    calculated = now + timedelta(
        seconds=retry_delay_seconds(
            attempts=attempts,
            base=settings.worker_retry_base_seconds,
            maximum=settings.worker_retry_max_seconds,
            jitter=settings.worker_retry_jitter_seconds,
            random_value=random_value,
        )
    )
    if retry_after_seconds is not None and retry_after_seconds >= 0:
        calculated = max(calculated, now + timedelta(seconds=retry_after_seconds))
    if operating_reset_at is not None:
        calculated = max(calculated, operating_reset_at)
    return calculated


class JobDataError(Exception):
    """Persisted job data is invalid and cannot be retried safely."""


class AutomationJobProcessor:
    """Deliver one claimed subscription event and persist a conditional transition."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        encryption: EncryptionService,
        portal_oauth: PortalOAuthService,
        settings: Settings | None = None,
        clock: Clock | None = None,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self._session_factory = session_factory
        self._encryption = encryption
        self._portal_oauth = portal_oauth
        self._settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._random = random_source

    async def process(self, job_id: UUID) -> None:
        loaded = await self._load(job_id)
        if loaded is None:
            return
        job, portal = loaded
        previous_error = job.last_error
        try:
            event_token, scheduled_at, requested_delay = self._validated_data(job, portal)
        except (JobDataError, EncryptionError):
            await self._failed(job, "invalid_persisted_job")
            return
        proposed_resume = self._clock()
        return_values = _return_values(job.id, scheduled_at, proposed_resume, requested_delay)
        try:
            response = await self._portal_oauth.call_portal(
                portal.id,
                "bizproc.event.send",
                {
                    "EVENT_TOKEN": event_token,
                    "RETURN_VALUES": return_values,
                    "LOG_MESSAGE": f"Короткая пауза завершена. Задание: {job.id}.",
                },
            )
            if response.result is not True:
                raise JobDataError("Bitrix24 did not confirm subscription completion")
        except BitrixPermissionError as exc:
            if exc.code == "ACCESS_DENIED":
                if previous_error == "event_delivery_outcome_unknown":
                    await self._failed(job, "event_delivery_requires_manual_review")
                else:
                    await self._expired(job, "event_token_invalid_or_expired")
            else:
                await self._failed(job, "event_delivery_permission_denied")
        except (
            BitrixAuthenticationError,
            BitrixConfigurationError,
            BitrixOAuthConfigurationError,
            BitrixOAuthRefreshRejectedError,
        ):
            await self._failed(job, "portal_authentication_failed")
        except BitrixPermanentError:
            await self._failed(job, "event_delivery_permanent_error")
        except BitrixRateLimitError as exc:
            await self._retry(job, "event_delivery_rate_limited", exc)
        except BitrixTemporaryError as exc:
            await self._retry(job, "event_delivery_temporary_error", exc)
        except BitrixInvalidResponseError as exc:
            await self._retry(job, "event_delivery_invalid_response", exc)
        except BitrixOAuthTemporaryError as exc:
            await self._retry(job, "oauth_temporarily_unavailable", exc)
        except BitrixTransportError as exc:
            await self._retry(job, "event_delivery_outcome_unknown", exc)
        except EncryptionError:
            await self._failed(job, "portal_token_decryption_failed")
        except JobDataError:
            await self._failed(job, "event_delivery_not_confirmed")
        else:
            async with self._session_factory() as session:
                await AutomationJobRepository(session).mark_completed(
                    job.id,
                    completed_at=proposed_resume,
                    return_values=return_values,
                )

    async def _load(self, job_id: UUID) -> tuple[AutomationJob, BitrixPortal | None] | None:
        async with self._session_factory() as session:
            job = await session.scalar(
                select(AutomationJob).where(
                    AutomationJob.id == job_id,
                    AutomationJob.status == "processing",
                )
            )
            if job is None:
                return None
            return job, await session.get(BitrixPortal, job.portal_id)

    def _validated_data(
        self, job: AutomationJob, portal: BitrixPortal | None
    ) -> tuple[str, datetime, int]:
        if (
            portal is None
            or portal.status != PortalStatus.ACTIVE.value
            or job.robot_code != SHORT_PAUSE_CODE
        ):
            raise JobDataError("Unsupported portal or robot")
        if not isinstance(job.payload, dict) or not job.event_token_encrypted:
            raise JobDataError("Invalid persisted job")
        requested = job.payload.get("requested_delay_seconds")
        scheduled_value = job.payload.get("scheduled_at")
        if isinstance(requested, bool) or not isinstance(requested, int) or requested < 0:
            raise JobDataError("Invalid persisted job")
        if not isinstance(scheduled_value, str):
            raise JobDataError("Invalid persisted job")
        try:
            scheduled = datetime.fromisoformat(scheduled_value)
        except ValueError as exc:
            raise JobDataError("Invalid persisted job") from exc
        if scheduled.tzinfo is None:
            raise JobDataError("Invalid persisted job")
        return (
            self._encryption.decrypt(job.event_token_encrypted),
            scheduled.astimezone(UTC),
            requested,
        )

    async def _retry(self, job: AutomationJob, code: str, error: Exception) -> None:
        async with self._session_factory() as session:
            repository = AutomationJobRepository(session)
            if job.attempts >= job.max_attempts:
                await repository.mark_failed(job.id, error_code=f"{code}_attempts_exhausted")
                return
            retry_after = getattr(error, "retry_after_seconds", None)
            operating_reset = _operating_reset(error)
            await repository.schedule_retry(
                job.id,
                run_at=retry_at(
                    now=self._clock(),
                    attempts=job.attempts,
                    settings=self._settings,
                    random_value=self._random(),
                    retry_after_seconds=retry_after,
                    operating_reset_at=operating_reset,
                ),
                error_code=code,
            )

    async def _failed(self, job: AutomationJob, code: str) -> None:
        async with self._session_factory() as session:
            await AutomationJobRepository(session).mark_failed(job.id, error_code=code)

    async def _expired(self, job: AutomationJob, code: str) -> None:
        async with self._session_factory() as session:
            await AutomationJobRepository(session).mark_expired(job.id, error_code=code)


def _return_values(
    job_id: UUID, scheduled_at: datetime, resumed_at: datetime, requested_delay: int
) -> dict[str, Any]:
    actual = max(0, int((resumed_at - scheduled_at).total_seconds()))
    return {
        "status": "completed",
        "job_id": str(job_id),
        "scheduled_at": scheduled_at.isoformat(),
        "resumed_at": resumed_at.isoformat(),
        "requested_delay_seconds": requested_delay,
        "actual_delay_seconds": actual,
    }


def _operating_reset(error: Exception) -> datetime | None:
    time = getattr(error, "time", None)
    value = time.get("operating_reset_at") if isinstance(time, Mapping) else None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed.astimezone(UTC) if parsed.tzinfo else None
        except ValueError:
            return None
    return None
