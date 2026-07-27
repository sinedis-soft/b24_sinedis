"""Opt-in PostgreSQL verification of SELECT FOR UPDATE refresh serialization."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bitrix.oauth import OAuthRefreshResult, PortalOAuthService
from app.config import Settings
from app.models.portal import BitrixPortal, PortalStatus
from app.security.encryption import EncryptionService


class CountingOAuth:
    def __init__(self) -> None:
        self.calls = 0

    async def refresh(self, **kwargs) -> OAuthRefreshResult:
        self.calls += 1
        await asyncio.sleep(0.05)
        return OAuthRefreshResult(
            member_id=kwargs["expected_member_id"],
            access_token="concurrent-new-access",
            refresh_token="concurrent-new-refresh",
            client_endpoint="https://portal.test/rest/",
            server_endpoint="https://oauth.test/rest/",
            expires_in=3600,
        )


async def test_concurrent_refresh_uses_one_new_pair() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.fail("TEST_DATABASE_URL must use postgresql+asyncpg://")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    encryption = EncryptionService(Fernet.generate_key().decode())
    portal_id = uuid4()
    member_id = f"oauth-concurrency-{portal_id.hex}"
    async with factory() as session, session.begin():
        session.add(
            BitrixPortal(
                id=portal_id,
                member_id=member_id,
                domain="portal.test",
                client_endpoint="https://portal.test/rest/",
                server_endpoint="https://oauth.test/rest/",
                access_token_encrypted=encryption.encrypt("old-access"),
                refresh_token_encrypted=encryption.encrypt("old-refresh"),
                token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
                status=PortalStatus.ACTIVE.value,
            )
        )

    transport = CountingOAuth()
    service = PortalOAuthService(
        session_factory=factory,
        encryption=encryption,
        oauth=transport,
        settings=Settings(),
    )
    try:
        results = await asyncio.gather(
            service.refresh_access_token(portal_id),
            service.refresh_access_token(portal_id),
        )
        assert results == ["concurrent-new-access", "concurrent-new-access"]
        assert transport.calls == 1
        async with factory() as session:
            stored = await session.get(BitrixPortal, portal_id)
            assert stored is not None
            assert encryption.decrypt(stored.access_token_encrypted) == "concurrent-new-access"
            assert encryption.decrypt(stored.refresh_token_encrypted) == "concurrent-new-refresh"
            assert stored.status == PortalStatus.ACTIVE.value
    finally:
        async with factory() as session, session.begin():
            await session.execute(delete(BitrixPortal).where(BitrixPortal.id == portal_id))
        await engine.dispose()
