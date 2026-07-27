"""HTTP intake endpoint for Bitrix24 robot subscriptions."""

import hashlib
import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.installation import read_callback_mapping
from app.config import Settings, get_settings
from app.database import get_db_session
from app.jobs.service import RobotCredentialError, ShortPauseJobService
from app.robots.payload import RobotPayloadError, normalize_robot_payload
from app.security.encryption import EncryptionService, get_encryption_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bitrix/robots", tags=["bitrix-robots"])


class ShortPauseResponse(BaseModel):
    ok: bool
    job_id: UUID
    status: str
    run_at: datetime
    existing: bool


async def get_short_pause_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    encryption: Annotated[EncryptionService, Depends(get_encryption_service)],
) -> ShortPauseJobService:
    return ShortPauseJobService(session, encryption)


@router.post("/short-pause", response_model=ShortPauseResponse)
async def short_pause_callback(
    request: Request,
    service: Annotated[ShortPauseJobService, Depends(get_short_pause_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ShortPauseResponse:
    try:
        callback = normalize_robot_payload(
            await read_callback_mapping(request),
            minimum_delay=settings.short_pause_min_seconds,
            maximum_delay=settings.short_pause_max_seconds,
        )
        result = await service.create(callback)
    except RobotPayloadError as exc:
        raise HTTPException(status_code=400, detail="Invalid robot payload") from exc
    except RobotCredentialError as exc:
        raise HTTPException(status_code=403, detail="Robot credential rejected") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503, detail="Job storage is temporarily unavailable"
        ) from exc
    logger.info(
        "Short-pause job accepted member=%s job=%s delay=%s existing=%s",
        hashlib.sha256(callback.member_id.encode()).hexdigest()[:12],
        result.job.id,
        callback.delay_seconds,
        result.existing,
    )
    return ShortPauseResponse(
        ok=True,
        job_id=result.job.id,
        status=result.job.status,
        run_at=result.job.run_at,
        existing=result.existing,
    )
