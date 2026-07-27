"""PostgreSQL-backed, race-safe automation job persistence."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import AutomationJob


@dataclass(frozen=True, slots=True)
class JobCreationResult:
    job: AutomationJob
    existing: bool


class AutomationJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_idempotent(self, values: dict[str, Any]) -> JobCreationResult:
        statement = (
            insert(AutomationJob)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[AutomationJob.portal_id, AutomationJob.event_token_hash]
            )
            .returning(AutomationJob.id)
        )
        inserted_id = await self._session.scalar(statement)
        predicate = (
            AutomationJob.id == inserted_id
            if inserted_id is not None
            else (
                (AutomationJob.portal_id == values["portal_id"])
                & (AutomationJob.event_token_hash == values["event_token_hash"])
            )
        )
        job = await self._session.scalar(select(AutomationJob).where(predicate))
        if job is None:
            raise RuntimeError("Automation job persistence failed")
        return JobCreationResult(job, inserted_id is None)

    async def claim_due_jobs(
        self, *, worker_id: str, limit: int, now: datetime
    ) -> list[AutomationJob]:
        """Claim a due batch in a short SKIP LOCKED transaction."""
        async with self._session.begin():
            jobs = list(
                await self._session.scalars(
                    select(AutomationJob)
                    .where(
                        AutomationJob.status.in_(["pending", "retry"]),
                        AutomationJob.run_at <= now,
                        AutomationJob.attempts < AutomationJob.max_attempts,
                    )
                    .order_by(AutomationJob.run_at, AutomationJob.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for job in jobs:
                job.status = "processing"
                job.locked_at = now
                job.locked_by = worker_id
                job.started_at = job.started_at or now
                job.attempts += 1
        return jobs

    async def get_processing_job(self, job_id: UUID) -> AutomationJob | None:
        async with self._session.begin():
            return await self._session.scalar(
                select(AutomationJob).where(
                    AutomationJob.id == job_id,
                    AutomationJob.status == "processing",
                )
            )

    async def mark_completed(
        self, job_id: UUID, *, completed_at: datetime, return_values: dict[str, Any]
    ) -> bool:
        return await self._transition(
            job_id,
            status="completed",
            completed_at=completed_at,
            return_values=return_values,
            last_error=None,
            locked_at=None,
            locked_by=None,
        )

    async def schedule_retry(self, job_id: UUID, *, run_at: datetime, error_code: str) -> bool:
        return await self._transition(
            job_id,
            status="retry",
            run_at=run_at,
            last_error=error_code,
            locked_at=None,
            locked_by=None,
        )

    async def mark_failed(self, job_id: UUID, *, error_code: str) -> bool:
        return await self._transition(
            job_id,
            status="failed",
            last_error=error_code,
            locked_at=None,
            locked_by=None,
        )

    async def mark_expired(self, job_id: UUID, *, error_code: str) -> bool:
        return await self._transition(
            job_id,
            status="expired",
            last_error=error_code,
            locked_at=None,
            locked_by=None,
        )

    async def _transition(self, job_id: UUID, **values: Any) -> bool:
        async with self._session.begin():
            result = await self._session.execute(
                update(AutomationJob)
                .where(AutomationJob.id == job_id, AutomationJob.status == "processing")
                .values(**values)
            )
        return bool(result.rowcount)

    async def recover_stale_jobs(self, *, now: datetime, lock_timeout: float) -> int:
        """Atomically recover only processing rows whose lease is stale."""
        cutoff = now - timedelta(seconds=lock_timeout)
        async with self._session.begin():
            result = await self._session.execute(
                update(AutomationJob)
                .where(
                    AutomationJob.status == "processing",
                    AutomationJob.locked_at < cutoff,
                )
                .values(
                    status=case(
                        (AutomationJob.attempts >= AutomationJob.max_attempts, "failed"),
                        else_="retry",
                    ),
                    run_at=case(
                        (AutomationJob.attempts < AutomationJob.max_attempts, now),
                        else_=AutomationJob.run_at,
                    ),
                    last_error=case(
                        (
                            AutomationJob.attempts >= AutomationJob.max_attempts,
                            "stale_processing_attempts_exhausted",
                        ),
                        else_="stale_processing_recovered",
                    ),
                    locked_at=None,
                    locked_by=None,
                    updated_at=func.now(),
                )
            )
        return int(result.rowcount or 0)
