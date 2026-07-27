"""Opt-in PostgreSQL tests for concurrent idempotent job intake."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.jobs.repository import AutomationJobRepository
from app.models.job import AutomationJob, JobStatus
from app.models.portal import BitrixPortal

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not configured")


async def test_concurrent_event_delivery_creates_one_job() -> None:
    assert DATABASE_URL and DATABASE_URL.startswith("postgresql+asyncpg://")
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    portal_id = uuid.uuid4()
    fingerprint = "1" * 64
    first_run = datetime.now(UTC) + timedelta(seconds=10)
    async with sessions.begin() as session:
        session.add(
            BitrixPortal(
                id=portal_id,
                member_id=f"job-test-{portal_id}",
                domain="portal.test",
                client_endpoint="https://portal.test/rest/",
                status="active",
            )
        )

    async def insert_job(run_at):
        async with sessions.begin() as session:
            return await AutomationJobRepository(session).create_idempotent(
                {
                    "portal_id": portal_id,
                    "robot_code": "sinedis.short_pause.v1",
                    "event_token_encrypted": "test-encrypted-value",
                    "event_token_hash": fingerprint,
                    "payload": {"requested_delay_seconds": 10},
                    "return_values": {"status": "pending", "requested_delay_seconds": 10},
                    "run_at": run_at,
                    "status": JobStatus.PENDING.value,
                    "attempts": 0,
                    "max_attempts": 10,
                }
            )

    try:
        results = await asyncio.gather(
            insert_job(first_run), insert_job(first_run + timedelta(seconds=30))
        )
        assert results[0].job.id == results[1].job.id
        assert results[0].job.run_at == results[1].job.run_at
        async with sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(AutomationJob)
                .where(
                    AutomationJob.portal_id == portal_id,
                    AutomationJob.event_token_hash == fingerprint,
                )
            )
            job = await session.scalar(
                select(AutomationJob).where(AutomationJob.portal_id == portal_id)
            )
            assert count == 1
            assert "event_token" not in job.payload and "application_token" not in job.payload
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(AutomationJob).where(AutomationJob.portal_id == portal_id))
            await session.execute(delete(BitrixPortal).where(BitrixPortal.id == portal_id))
        await engine.dispose()
