"""Bitrix24 local-application installation and uninstall callbacks."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.bitrix.client import BitrixClient
from app.bitrix.exceptions import (
    BitrixAuthenticationError,
    BitrixConfigurationError,
    BitrixInvalidResponseError,
    BitrixPermanentError,
    BitrixPermissionError,
    BitrixRateLimitError,
    BitrixTemporaryError,
    BitrixTransportError,
)
from app.bitrix.oauth import token_expiry
from app.bitrix.payload import BitrixEventPayload, BitrixPayloadError, normalize_event_payload
from app.bitrix.registration import BitrixRegistrationService
from app.bitrix.security import hash_application_token, verify_application_token
from app.database import get_db_session
from app.models.portal import BitrixPortal, PortalStatus
from app.security.encryption import EncryptionService, get_encryption_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bitrix", tags=["bitrix-installation"])


class LifecycleResponse(BaseModel):
    """Safe public lifecycle callback response."""

    ok: bool
    member_id: str
    status: str
    robots: Mapping[str, str] = Field(default_factory=dict)
    events: Mapping[str, str] = Field(default_factory=dict)


class LifecycleCredentialError(Exception):
    """Lifecycle callback credentials cannot be confirmed."""


class ApplicationLifecycleService:
    """Validate, register, and persist the local-application lifecycle."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        encryption: EncryptionService,
        registration: BitrixRegistrationService | None = None,
    ) -> None:
        self._session = session
        self._encryption = encryption
        self._registration = registration or BitrixRegistrationService()

    async def install(self, payload: BitrixEventPayload) -> LifecycleResponse:
        """Verify app context, then atomically upsert one portal by member_id."""
        auth = payload.auth
        if (
            auth.access_token is None
            or auth.refresh_token is None
            or auth.application_token is None
        ):
            raise BitrixPayloadError("OAuth installation credentials are missing")
        async with BitrixClient(
            client_endpoint=auth.client_endpoint, access_token=auth.access_token
        ) as client:
            await client.call("app.info")
            registration = await self._registration.ensure_all(client)
        expires_at = token_expiry(
            expires=auth.expires,
            expires_in=auth.expires_in,
            now=_utc_now(),
        )
        values = {
            "member_id": auth.member_id,
            "domain": auth.domain,
            "client_endpoint": auth.client_endpoint,
            "server_endpoint": auth.server_endpoint,
            "access_token_encrypted": self._encryption.encrypt(auth.access_token),
            "refresh_token_encrypted": self._encryption.encrypt(auth.refresh_token),
            "token_expires_at": expires_at,
            "application_token_hash": hash_application_token(auth.application_token),
            "status": PortalStatus.ACTIVE.value,
        }
        statement = insert(BitrixPortal).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[BitrixPortal.member_id],
            set_={key: value for key, value in values.items() if key != "member_id"},
        )
        async with self._session.begin():
            await self._session.execute(statement)
        logger.info(
            "Bitrix24 installation completed member=%s domain=%s",
            _member_fingerprint(auth.member_id),
            auth.domain,
        )
        return LifecycleResponse(
            ok=True,
            member_id=auth.member_id,
            status="active",
            robots=registration.robots,
            events=registration.events,
        )

    async def uninstall(self, payload: BitrixEventPayload) -> LifecycleResponse:
        """Authenticate uninstall and mark the portal inactive without deleting history."""
        auth = payload.auth
        if auth.application_token is None:
            raise BitrixPayloadError("Application token is missing")
        async with self._session.begin():
            portal = await self._session.scalar(
                select(BitrixPortal)
                .where(BitrixPortal.member_id == auth.member_id)
                .with_for_update()
            )
            if (
                portal is None
                or portal.application_token_hash is None
                or not verify_application_token(
                    auth.application_token, portal.application_token_hash
                )
            ):
                raise LifecycleCredentialError("Lifecycle credential is invalid")
            portal.status = PortalStatus.INACTIVE.value
        clean_requested = str(payload.data.get("CLEAN", "0")) == "1"
        logger.info(
            "Bitrix24 uninstall completed member=%s clean_requested=%s history_preserved=true",
            _member_fingerprint(auth.member_id),
            clean_requested,
        )
        return LifecycleResponse(ok=True, member_id=auth.member_id, status="inactive")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _member_fingerprint(member_id: str) -> str:
    return hashlib.sha256(member_id.encode()).hexdigest()[:12]


async def read_callback_mapping(request: Request) -> Mapping[str, Any]:
    """Read JSON, form-urlencoded, or multipart data without logging the source body."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    try:
        if content_type == "application/json":
            value = await request.json()
            if not isinstance(value, Mapping):
                raise BitrixPayloadError("JSON payload must be an object")
            return dict(value)
        if content_type in {
            "application/x-www-form-urlencoded",
            "multipart/form-data",
        }:
            form = await request.form()
            return dict(form.multi_items())
    except BitrixPayloadError:
        raise
    except Exception as exc:
        raise BitrixPayloadError("Callback payload cannot be parsed") from exc
    raise BitrixPayloadError("Unsupported callback content type")


async def get_lifecycle_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    encryption: Annotated[EncryptionService, Depends(get_encryption_service)],
) -> ApplicationLifecycleService:
    return ApplicationLifecycleService(session=session, encryption=encryption)


@router.post("/install", response_model=LifecycleResponse)
async def install_callback(
    request: Request,
    service: Annotated[ApplicationLifecycleService, Depends(get_lifecycle_service)],
) -> LifecycleResponse:
    """Install or idempotently reinstall one API-only local application."""
    try:
        raw = await read_callback_mapping(request)
        payload = normalize_event_payload(
            raw, expected_event="ONAPPINSTALL", require_oauth_tokens=True
        )
        return await service.install(payload)
    except BitrixPayloadError as exc:
        raise HTTPException(status_code=400, detail="Invalid installation payload") from exc
    except BitrixAuthenticationError as exc:
        raise HTTPException(status_code=401, detail="Installation credential rejected") from exc
    except BitrixPermissionError as exc:
        raise HTTPException(status_code=403, detail="Installation credential rejected") from exc
    except (BitrixTemporaryError, BitrixTransportError, BitrixRateLimitError) as exc:
        raise HTTPException(status_code=503, detail="Bitrix24 is temporarily unavailable") from exc
    except BitrixInvalidResponseError as exc:
        raise HTTPException(status_code=503, detail="Bitrix24 is temporarily unavailable") from exc
    except (BitrixConfigurationError, BitrixPermanentError) as exc:
        raise HTTPException(status_code=500, detail="Application registration failed") from exc


@router.post("/uninstall", response_model=LifecycleResponse)
async def uninstall_callback(
    request: Request,
    service: Annotated[ApplicationLifecycleService, Depends(get_lifecycle_service)],
) -> LifecycleResponse:
    """Deactivate a portal after application-token verification."""
    try:
        raw = await read_callback_mapping(request)
        payload = normalize_event_payload(
            raw, expected_event="ONAPPUNINSTALL", require_oauth_tokens=False
        )
        return await service.uninstall(payload)
    except BitrixPayloadError as exc:
        raise HTTPException(status_code=400, detail="Invalid uninstall payload") from exc
    except LifecycleCredentialError as exc:
        raise HTTPException(status_code=403, detail="Uninstall credential rejected") from exc
