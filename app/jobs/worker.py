"""Standalone polling worker for durable Bitrix24 automation jobs."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx

from app.bitrix.oauth import BitrixOAuthService, PortalOAuthService
from app.config import Settings, get_settings
from app.database import dispose_database_engine, get_session_factory
from app.jobs.processor import AutomationJobProcessor
from app.jobs.recovery import JobRecoveryService
from app.jobs.repository import AutomationJobRepository
from app.logging import configure_logging
from app.security.encryption import get_encryption_service

logger = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]


async def worker_loop(
    *,
    settings: Settings,
    shutdown: asyncio.Event,
    processor: AutomationJobProcessor,
    recovery: JobRecoveryService,
    worker_id: str,
    once: bool = False,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Run recovery and polling with injectable collaborators for deterministic tests."""
    session_factory = get_session_factory()
    next_recovery = datetime.min.replace(tzinfo=UTC)
    while not shutdown.is_set():
        now = datetime.now(UTC)
        if now >= next_recovery:
            recovered = await recovery.recover(
                now=now, lock_timeout=settings.worker_lock_timeout_seconds
            )
            logger.info("Worker recovery worker=%s recovered=%s", worker_id, recovered)
            next_recovery = now + timedelta(seconds=settings.worker_recovery_interval_seconds)
        if shutdown.is_set():
            break
        async with session_factory() as session:
            jobs = await AutomationJobRepository(session).claim_due_jobs(
                worker_id=worker_id,
                limit=settings.worker_batch_size,
                now=datetime.now(UTC),
            )
        for job in jobs:
            if shutdown.is_set():
                break
            logger.info(
                "Worker processing worker=%s job=%s robot=%s attempt=%s",
                worker_id,
                job.id,
                job.robot_code,
                job.attempts,
            )
            try:
                await processor.process(job.id)
            except Exception:
                logger.exception("Unexpected worker processing failure job=%s", job.id)
        if once:
            return
        if not shutdown.is_set():
            await sleep(settings.worker_poll_interval_seconds)


async def run_worker(*, once: bool = False) -> None:
    """Create process resources, run the loop, and release them on shutdown."""
    settings = get_settings()
    configure_logging(settings)
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, shutdown.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(shutdown.set))
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
    session_factory = get_session_factory()
    encryption = get_encryption_service()
    rest_client = httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(
            connect=settings.bitrix_http_connect_timeout_seconds,
            read=settings.bitrix_http_read_timeout_seconds,
            write=settings.bitrix_http_write_timeout_seconds,
            pool=settings.bitrix_http_pool_timeout_seconds,
        ),
        limits=httpx.Limits(
            max_connections=settings.bitrix_http_max_connections,
            max_keepalive_connections=settings.bitrix_http_max_connections,
        ),
    )
    oauth = BitrixOAuthService(settings=settings)
    portal_oauth = PortalOAuthService(
        session_factory=session_factory,
        encryption=encryption,
        oauth=oauth,
        settings=settings,
        rest_http_client=rest_client,
    )
    processor = AutomationJobProcessor(
        session_factory=session_factory,
        encryption=encryption,
        portal_oauth=portal_oauth,
        settings=settings,
    )
    recovery = JobRecoveryService(session_factory)
    logger.info("Worker started worker=%s", worker_id)
    try:
        await worker_loop(
            settings=settings,
            shutdown=shutdown,
            processor=processor,
            recovery=recovery,
            worker_id=worker_id,
            once=once,
        )
    finally:
        await oauth.aclose()
        await rest_client.aclose()
        await dispose_database_engine()
        logger.info("Worker stopped worker=%s", worker_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Process durable Bitrix24 automation jobs")
    parser.add_argument("--once", action="store_true", help="recover and process one due batch")
    args = parser.parse_args()
    asyncio.run(run_worker(once=args.once))


if __name__ == "__main__":
    main()
