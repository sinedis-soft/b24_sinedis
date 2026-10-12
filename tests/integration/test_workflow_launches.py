"""Opt-in PostgreSQL tests: competing button clicks reserve one remote launch."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.workflow_buttons import Actor, ButtonSettings, LaunchRequest, launch
from app.models import BitrixPortal, WorkflowButton, WorkflowLaunch

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not configured")


@pytest.mark.parametrize("same_key", [True, False])
async def test_concurrent_clicks_start_once(same_key):
    assert DATABASE_URL and DATABASE_URL.startswith("postgresql+asyncpg://")
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    portal = BitrixPortal(
        id=uuid4(),
        member_id=str(uuid4()),
        domain="portal.test",
        client_endpoint="https://portal.test/rest/",
        status="active",
    )
    button = WorkflowButton(
        id=uuid4(),
        portal_id=portal.id,
        field_name="UF_CRM_BUTTON",
        settings=ButtonSettings(template_id=125, enabled=True, allowed_user_ids=[17]).model_dump(),
    )
    async with sessions.begin() as session:
        session.add(portal)
        await session.flush()
        session.add(button)
    server = AsyncMock()
    server.call_portal.return_value = SimpleNamespace(result="workflow-id")
    actor = Actor(portal, 17, False, AsyncMock(), server)
    request_id = uuid4()

    async def click(key):
        async with sessions() as session:
            try:
                return await launch(
                    LaunchRequest(
                        member_id=portal.member_id,
                        access_token="test",
                        field_name=button.field_name,
                        deal_id=42,
                        request_id=key,
                    ),
                    actor,
                    session,
                )
            except HTTPException as exc:
                return exc.status_code

    try:
        results = await asyncio.gather(
            click(request_id), click(request_id if same_key else uuid4())
        )
        server.call_portal.assert_awaited_once()
        if same_key:
            assert results[0]["launch_id"] == results[1]["launch_id"]
        else:
            assert 409 in results
        async with sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(WorkflowLaunch)
                .where(WorkflowLaunch.portal_id == portal.id)
            )
            assert count == 1
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(WorkflowLaunch).where(WorkflowLaunch.portal_id == portal.id)
            )
            await session.execute(
                delete(WorkflowButton).where(WorkflowButton.portal_id == portal.id)
            )
            await session.execute(delete(BitrixPortal).where(BitrixPortal.id == portal.id))
        await engine.dispose()
