"""Liveness and readiness HTTP endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]


class HealthResponse(BaseModel):
    """Public liveness response."""

    ok: bool
    service: str
    version: str


class ReadinessResponse(BaseModel):
    """Public readiness response."""

    ok: bool
    database: str


@router.get("/health", response_model=HealthResponse)
async def health(settings: SettingsDependency) -> HealthResponse:
    """Report that the API process is alive."""
    return HealthResponse(ok=True, service=settings.service_name, version=settings.app_version)


@router.get("/ready", response_model=ReadinessResponse)
async def ready(response: Response, session: DatabaseSession) -> ReadinessResponse:
    """Verify PostgreSQL connectivity without exposing connection details."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # The boundary converts any driver/pool failure to a safe response.
        logger.warning("Database readiness check failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(ok=False, database="unavailable")
    return ReadinessResponse(ok=True, database="available")
