"""Safely synchronize extensions for all active installed portals."""

import asyncio
import hashlib

from sqlalchemy import select

from app.bitrix.client import BitrixClient
from app.bitrix.oauth import BitrixOAuthService, PortalOAuthService
from app.bitrix.registration import BitrixRegistrationService
from app.config import get_settings
from app.database import dispose_database_engine, get_session_factory
from app.models.portal import BitrixPortal, PortalStatus
from app.security.encryption import get_encryption_service


async def synchronize() -> None:
    """Synchronize without printing member IDs, domains, or credentials."""
    settings = get_settings()
    factory = get_session_factory()
    encryption = get_encryption_service()
    async with BitrixOAuthService(settings=settings) as oauth:
        portal_oauth = PortalOAuthService(
            session_factory=factory, encryption=encryption, oauth=oauth, settings=settings
        )
        async with factory() as session:
            portal_ids = list(
                await session.scalars(
                    select(BitrixPortal.id).where(BitrixPortal.status == PortalStatus.ACTIVE.value)
                )
            )
        for portal_id in portal_ids:
            token = await portal_oauth.get_access_token(portal_id)
            async with factory() as session:
                portal = await session.get(BitrixPortal, portal_id)
                assert portal is not None
                endpoint, fingerprint = (
                    portal.client_endpoint,
                    hashlib.sha256(portal.member_id.encode()).hexdigest()[:12],
                )
            async with BitrixClient(client_endpoint=endpoint, access_token=token) as client:
                result = await BitrixRegistrationService(settings=settings).ensure_all(client)
            print(f"Portal: {fingerprint}")
            print("Activities:")
            for code, status in result.activities.items():
                print(f"  {code}: {status}")
            print("Robots:")
            for code, status in result.robots.items():
                print(f"  {code}: {status}")


def main() -> None:
    try:
        asyncio.run(synchronize())
    finally:
        asyncio.run(dispose_database_engine())


if __name__ == "__main__":
    main()
