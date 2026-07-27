"""Opt-in model integration tests for an explicitly configured PostgreSQL test database."""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AutomationJob, BitrixPortal


@pytest.fixture
async def postgres_session() -> AsyncIterator[AsyncSession]:
    """Open a rollback-only transaction against an explicit, already-migrated test database."""
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.fail("TEST_DATABASE_URL must use postgresql+asyncpg://")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    connection = await engine.connect()
    transaction = await connection.begin()
    factory = async_sessionmaker(bind=connection, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


async def test_insert_portal_job_and_jsonb(postgres_session: AsyncSession) -> None:
    """Portal and JSONB job values round-trip through PostgreSQL."""
    portal = BitrixPortal(
        member_id="integration-member",
        domain="integration.example",
        client_endpoint="https://integration.example/rest/",
    )
    postgres_session.add(portal)
    await postgres_session.flush()

    job = AutomationJob(
        portal_id=portal.id,
        robot_code="sinedis.short_pause.v1",
        event_token_encrypted="encrypted-test-value",
        event_token_hash="a" * 64,
        payload={"delay_seconds": 10},
        return_values={"status": "pending"},
        run_at=datetime.now(UTC),
    )
    postgres_session.add(job)
    await postgres_session.flush()
    postgres_session.expire(job)

    stored = await postgres_session.scalar(select(AutomationJob).where(AutomationJob.id == job.id))
    assert stored is not None
    assert stored.payload == {"delay_seconds": 10}
    assert stored.return_values == {"status": "pending"}


async def test_event_token_hash_is_unique_per_portal(postgres_session: AsyncSession) -> None:
    """Duplicate event tokens cannot create two jobs for one portal."""
    portal = BitrixPortal(
        member_id="unique-member",
        domain="unique.example",
        client_endpoint="https://unique.example/rest/",
    )
    postgres_session.add(portal)
    await postgres_session.flush()
    values = {
        "portal_id": portal.id,
        "robot_code": "sinedis.short_pause.v1",
        "event_token_encrypted": "encrypted-test-value",
        "event_token_hash": "b" * 64,
        "run_at": datetime.now(UTC),
    }
    postgres_session.add(AutomationJob(**values))
    await postgres_session.flush()

    with pytest.raises(IntegrityError):
        async with postgres_session.begin_nested():
            postgres_session.add(AutomationJob(**values))
            await postgres_session.flush()


async def test_job_requires_existing_portal(postgres_session: AsyncSession) -> None:
    """PostgreSQL enforces the portal foreign key."""
    job = AutomationJob(
        portal_id=uuid.uuid4(),
        robot_code="sinedis.short_pause.v1",
        event_token_encrypted="encrypted-test-value",
        event_token_hash="c" * 64,
        run_at=datetime.now(UTC),
    )
    with pytest.raises(IntegrityError):
        async with postgres_session.begin_nested():
            postgres_session.add(job)
            await postgres_session.flush()
