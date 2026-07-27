"""Recovery service for abandoned processing leases."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.jobs.repository import AutomationJobRepository


class JobRecoveryService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def recover(self, *, now: datetime, lock_timeout: float) -> int:
        async with self._session_factory() as session:
            return await AutomationJobRepository(session).recover_stale_jobs(
                now=now, lock_timeout=lock_timeout
            )
