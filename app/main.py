"""FastAPI application factory and ASGI entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.installation import router as installation_router
from app.api.robots import router as robots_router
from app.config import get_settings
from app.database import dispose_database_engine
from app.logging import configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Release pooled database connections during application shutdown."""
    yield
    await dispose_database_engine()


def create_app() -> FastAPI:
    """Build and configure a FastAPI application instance."""
    settings = get_settings()
    configure_logging(settings)
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.app_env == "development" else None,
        redoc_url="/redoc" if settings.app_env == "development" else None,
        openapi_url="/openapi.json" if settings.app_env == "development" else None,
        lifespan=lifespan,
    )
    application.include_router(health_router)
    application.include_router(installation_router)
    application.include_router(robots_router)
    return application


app = create_app()
