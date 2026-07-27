"""Unit-level repository claim and worker loop checks."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.config import Settings
from app.jobs.repository import AutomationJobRepository
from app.jobs.worker import worker_loop


class ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)


class Session:
    def __init__(self, jobs=()):
        self.jobs, self.statement = list(jobs), None

    @asynccontextmanager
    async def begin(self):
        yield

    async def scalars(self, statement):
        self.statement = statement
        return ScalarRows(self.jobs)


class FactoryContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return None


class Factory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return FactoryContext(self.session)


async def test_claim_sets_lease_and_increments_attempt_once():
    now = datetime(2026, 7, 27, tzinfo=UTC)
    job = SimpleNamespace(
        status="pending", locked_at=None, locked_by=None, started_at=None, attempts=0
    )
    session = Session([job])
    claimed = await AutomationJobRepository(session).claim_due_jobs(
        worker_id="worker-test", limit=5, now=now
    )
    sql = str(session.statement.compile()).upper()
    assert "FOR UPDATE" in sql and "SKIP LOCKED" in sql
    assert "RUN_AT" in sql and "ATTEMPTS" in sql and "MAX_ATTEMPTS" in sql
    assert claimed == [job]
    assert (job.status, job.locked_at, job.locked_by, job.started_at, job.attempts) == (
        "processing",
        now,
        "worker-test",
        now,
        1,
    )


class Recovery:
    def __init__(self):
        self.calls = 0

    async def recover(self, **kwargs):
        self.calls += 1
        return 0


class Processor:
    def __init__(self):
        self.ids = []

    async def process(self, job_id):
        self.ids.append(job_id)


async def test_worker_once_recovers_claims_and_stops(monkeypatch):
    job = SimpleNamespace(id=uuid4(), robot_code="sinedis.short_pause.v1", attempts=1)
    session = Session([job])
    monkeypatch.setattr("app.jobs.worker.get_session_factory", lambda: Factory(session))
    recovery, processor = Recovery(), Processor()
    import asyncio

    await worker_loop(
        settings=Settings(),
        shutdown=asyncio.Event(),
        processor=processor,
        recovery=recovery,
        worker_id="worker-test",
        once=True,
    )
    assert recovery.calls == 1 and processor.ids == [job.id]


class Result:
    rowcount = 2


class UpdateSession(Session):
    async def execute(self, statement):
        self.statement = statement
        return Result()


async def test_recovery_is_limited_to_stale_processing_and_clears_locks():
    session = UpdateSession()
    count = await AutomationJobRepository(session).recover_stale_jobs(
        now=datetime(2026, 7, 27, tzinfo=UTC), lock_timeout=120
    )
    compiled = session.statement.compile().params
    sql = str(session.statement.compile()).lower()
    assert count == 2
    assert "processing" in compiled.values()
    assert "locked_at" in sql and "status" in sql and "attempts" in sql
    assert None in compiled.values()
