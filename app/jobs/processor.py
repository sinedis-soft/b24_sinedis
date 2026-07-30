"""Process claimed automation jobs and resume Bitrix24 subscriptions."""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from jsonpath_ng import parse as parse_jsonpath
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
from app.robots.rest_request import REST_REQUEST_ROBOT_CODE
from app.robots.short_pause import SHORT_PAUSE_CODE
from app.robots.wait_field import WAIT_FIELD_ROBOT_CODE
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
        if job.robot_code == REST_REQUEST_ROBOT_CODE:
            await self._process_rest_request(job, portal)
            return
        if job.robot_code == WAIT_FIELD_ROBOT_CODE:
            await self._process_wait_field(job, portal)
            return
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

    async def _process_rest_request(self, job: AutomationJob, portal: BitrixPortal | None) -> None:
        try:
            event_token, payload = self._subscription_data(job, portal)
        except (JobDataError, EncryptionError):
            await self._failed(job, "invalid_persisted_job")
            return
        return_values = job.return_values if payload.get("request_completed") else None
        if not isinstance(return_values, dict):
            try:
                response = await self._portal_oauth.call_portal(
                    job.portal_id, payload["rest_method"], payload["request_params"]
                )
                matches = [
                    match.value
                    for match in parse_jsonpath(payload["jsonpath"]).find(response.result)
                ]
                selected = None if not matches else matches[0] if len(matches) == 1 else matches
                result_json = json.dumps(
                    selected, ensure_ascii=False, separators=(",", ":"), default=str
                )
                if selected is None:
                    result_text = ""
                elif isinstance(selected, str):
                    result_text = selected
                elif isinstance(selected, (int, float, bool)):
                    result_text = str(selected)
                else:
                    result_text = result_json
                return_values = {
                    "status": "completed",
                    "job_id": str(job.id),
                    "result_text": result_text,
                    "result_json": result_json,
                    "matches_count": len(matches),
                    "error_code": "",
                    "error_message": "",
                }
                payload["request_completed"] = True
                async with self._session_factory() as session:
                    await AutomationJobRepository(session).save_processing_result(
                        job.id, payload=payload, return_values=return_values
                    )
            except BitrixRateLimitError as exc:
                await self._retry(job, "rest_request_rate_limited", exc)
                return
            except BitrixOAuthTemporaryError as exc:
                await self._retry(job, "oauth_temporarily_unavailable", exc)
                return
            except BitrixTransportError:
                return_values = self._error_values(
                    job,
                    "rest_request_outcome_unknown",
                    "Ответ портала не получен; результат выполнения REST-метода неизвестен.",
                )
            except (
                BitrixPermissionError,
                BitrixPermanentError,
                BitrixInvalidResponseError,
                BitrixAuthenticationError,
                BitrixConfigurationError,
                BitrixOAuthConfigurationError,
                BitrixOAuthRefreshRejectedError,
            ):
                return_values = self._error_values(
                    job, "rest_request_failed", "REST-запрос Bitrix24 завершился ошибкой."
                )
            if not payload.get("request_completed"):
                payload["request_completed"] = True
                async with self._session_factory() as session:
                    await AutomationJobRepository(session).save_processing_result(
                        job.id, payload=payload, return_values=return_values
                    )
                await self._notify(job, payload, return_values["error_message"])
        await self._deliver_subscription(job, event_token, return_values)

    async def _process_wait_field(self, job: AutomationJob, portal: BitrixPortal | None) -> None:
        try:
            event_token, payload = self._subscription_data(job, portal)
            created = datetime.fromisoformat(payload["created_at"]).astimezone(UTC)
            checks = int(payload.get("checks_count", 0)) + 1
            response = await self._portal_oauth.call_portal(
                job.portal_id,
                "crm.item.get",
                {
                    "entityTypeId": payload["entity_type_id"],
                    "id": payload["entity_id"],
                    "useOriginalUfNames": True,
                },
            )
            if not isinstance(response.result, Mapping) or not isinstance(
                response.result.get("item"), Mapping
            ):
                raise BitrixInvalidResponseError(method="crm.item.get", http_status=200)
            value = response.result["item"].get(payload["field_name"])
            normalized = value.strip() if isinstance(value, str) else value
            now = self._clock()
            payload["checks_count"] = checks
            if normalized in (None, "", [], {}):
                if now < created + timedelta(seconds=payload["timeout_seconds"]):
                    async with self._session_factory() as session:
                        await AutomationJobRepository(session).schedule_poll(
                            job.id,
                            run_at=now + timedelta(seconds=payload["poll_interval_seconds"]),
                            payload=payload,
                        )
                    return
                values = {
                    "status": "timeout",
                    "job_id": str(job.id),
                    "field_value": "",
                    "checks_count": checks,
                    "completed_at": "",
                    "error_code": "field_wait_timeout",
                    "error_message": "Поле не было заполнено за установленное время.",
                }
                await self._notify(job, payload, values["error_message"])
            else:
                serialized = (
                    normalized
                    if isinstance(normalized, str)
                    else json.dumps(
                        normalized, ensure_ascii=False, separators=(",", ":"), default=str
                    )
                )
                values = {
                    "status": "completed",
                    "job_id": str(job.id),
                    "field_value": serialized,
                    "checks_count": checks,
                    "completed_at": now.isoformat(),
                    "error_code": "",
                    "error_message": "",
                }
        except (JobDataError, EncryptionError, KeyError, TypeError, ValueError):
            await self._failed(job, "invalid_persisted_job")
            return
        except (BitrixRateLimitError, BitrixTemporaryError, BitrixOAuthTemporaryError) as exc:
            await self._retry(job, "field_check_temporary_error", exc)
            return
        except (
            BitrixPermissionError,
            BitrixPermanentError,
            BitrixInvalidResponseError,
            BitrixAuthenticationError,
            BitrixConfigurationError,
            BitrixOAuthConfigurationError,
            BitrixOAuthRefreshRejectedError,
            BitrixTransportError,
        ):
            values = self._error_values(
                job,
                "field_check_failed",
                "Не удалось проверить поле CRM.",  # noqa: RUF001
            )
            await self._notify(job, payload, values["error_message"])
        await self._deliver_subscription(job, event_token, values)

    def _subscription_data(
        self, job: AutomationJob, portal: BitrixPortal | None
    ) -> tuple[str, dict[str, Any]]:
        if portal is None or portal.status != PortalStatus.ACTIVE.value:
            raise JobDataError("Unsupported portal")
        if not isinstance(job.payload, dict) or not job.event_token_encrypted:
            raise JobDataError("Invalid persisted job")
        return self._encryption.decrypt(job.event_token_encrypted), dict(job.payload)

    async def _deliver_subscription(
        self, job: AutomationJob, event_token: str, return_values: dict[str, Any]
    ) -> None:
        try:
            response = await self._portal_oauth.call_portal(
                job.portal_id,
                "bizproc.event.send",
                {
                    "EVENT_TOKEN": event_token,
                    "RETURN_VALUES": return_values,
                    "LOG_MESSAGE": f"Задание {job.id} завершено.",
                },
            )
            if response.result is not True:
                raise JobDataError("Subscription was not confirmed")
        except (
            BitrixRateLimitError,
            BitrixTemporaryError,
            BitrixOAuthTemporaryError,
            BitrixInvalidResponseError,
            BitrixTransportError,
        ) as exc:
            await self._retry(job, "event_delivery_temporary_error", exc)
            return
        except Exception:
            await self._failed(job, "event_delivery_failed")
            return
        async with self._session_factory() as session:
            await AutomationJobRepository(session).mark_completed(
                job.id, completed_at=self._clock(), return_values=return_values
            )

    async def _notify(self, job: AutomationJob, payload: dict[str, Any], message: str) -> None:
        for user_id in payload.get("error_recipients", []):
            try:
                await self._portal_oauth.call_portal(
                    job.portal_id,
                    "im.notify.system.add",
                    {
                        "USER_ID": user_id,
                        "MESSAGE": message,
                        "MESSAGE_OUT": message,
                    },
                )
            except Exception:
                # Notification is best effort and never masks the workflow result.
                continue

    @staticmethod
    def _error_values(job: AutomationJob, code: str, message: str) -> dict[str, Any]:
        return {
            "status": "error",
            "job_id": str(job.id),
            "result_text": "",
            "result_json": "null",
            "matches_count": 0,
            "error_code": code,
            "error_message": message,
        }

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
