"""Opt-in PostgreSQL concurrency tests for worker claims and recovery."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.jobs.repository import AutomationJobRepository
from app.models.job import AutomationJob
from app.models.portal import BitrixPortal

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not configured")


async def test_two_workers_claim_once_and_recover_stale_lease():
    assert DATABASE_URL and DATABASE_URL.startswith("postgresql+asyncpg://")
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    portal_id, job_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        session.add(
            BitrixPortal(
                id=portal_id,
                member_id=f"worker-test-{portal_id}",
                domain="portal.test",
                client_endpoint="https://portal.test/rest/",
                status="active",
            )
        )
        session.add(
            AutomationJob(
                id=job_id,
                portal_id=portal_id,
                robot_code="sinedis.short_pause.v1",
                event_token_encrypted="test-encrypted",
                event_token_hash="2" * 64,
                payload={"requested_delay_seconds": 1, "scheduled_at": now.isoformat()},
                return_values={},
                run_at=now - timedelta(seconds=1),
                status="pending",
                attempts=0,
                max_attempts=2,
            )
        )

    async def claim(worker):
        async with sessions() as session:
            return await AutomationJobRepository(session).claim_due_jobs(
                worker_id=worker, limit=1, now=now
            )

    try:
        claimed = await asyncio.gather(claim("worker-a"), claim("worker-b"))
        assert sum(len(batch) for batch in claimed) == 1
        async with sessions() as session:
            job = await session.get(AutomationJob, job_id)
            assert job.status == "processing" and job.attempts == 1
            job.locked_at = now - timedelta(minutes=10)
            await session.commit()
        async with sessions() as session:
            count = await AutomationJobRepository(session).recover_stale_jobs(
                now=now, lock_timeout=120
            )
        assert count == 1
        async with sessions() as session:
            job = await session.get(AutomationJob, job_id)
            assert job.status == "retry" and job.locked_at is None and job.locked_by is None
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(AutomationJob).where(AutomationJob.portal_id == portal_id))
            await session.execute(delete(BitrixPortal).where(BitrixPortal.id == portal_id))
        await engine.dispose()
