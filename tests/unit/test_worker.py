"""Unit tests for worker retry calculations and event delivery contract."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.bitrix.client import BitrixResponse
from app.config import Settings
from app.jobs.processor import AutomationJobProcessor, retry_at, retry_delay_seconds


class Context:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *args):
        return None


class Factory:
    def __call__(self):
        return Context()


class Encryption:
    def decrypt(self, value):
        assert value == "fernet:v1:encrypted-test-value"
        return "test-event-token"


class OAuth:
    def __init__(self):
        self.calls = []

    async def call_portal(self, portal_id, method, params):
        self.calls.append((portal_id, method, params))
        return BitrixResponse(result=True)


class Repository:
    completed = None

    def __init__(self, session):
        pass

    async def mark_completed(self, job_id, **values):
        Repository.completed = (job_id, values)
        return True


def test_exponential_backoff_is_bounded_and_jittered():
    assert retry_delay_seconds(attempts=1, base=5, maximum=300, jitter=2, random_value=0) == 5
    assert retry_delay_seconds(attempts=2, base=5, maximum=300, jitter=2, random_value=0.5) == 11
    assert retry_delay_seconds(attempts=20, base=5, maximum=300, jitter=2, random_value=1) == 300


def test_retry_after_and_operating_reset_choose_latest():
    now = datetime(2026, 7, 27, tzinfo=UTC)
    settings = Settings(
        WORKER_RETRY_BASE_SECONDS=5, WORKER_RETRY_MAX_SECONDS=300, WORKER_RETRY_JITTER_SECONDS=0
    )
    reset = datetime(2026, 7, 27, 0, 2, tzinfo=UTC)
    assert (
        retry_at(
            now=now,
            attempts=1,
            settings=settings,
            random_value=0,
            retry_after_seconds=60,
            operating_reset_at=reset,
        )
        == reset
    )


async def test_processor_sends_exact_subscription_contract(monkeypatch):
    job_id, portal_id = uuid4(), uuid4()
    scheduled = datetime(2026, 7, 27, 12, tzinfo=UTC)
    resumed = datetime(2026, 7, 27, 12, 0, 11, tzinfo=UTC)
    job = SimpleNamespace(
        id=job_id,
        portal_id=portal_id,
        status="processing",
        robot_code="sinedis.short_pause.v1",
        event_token_encrypted="fernet:v1:encrypted-test-value",
        payload={"requested_delay_seconds": 10, "scheduled_at": scheduled.isoformat()},
        last_error=None,
        attempts=1,
        max_attempts=10,
    )
    portal = SimpleNamespace(id=portal_id, status="active")
    oauth = OAuth()
    processor = AutomationJobProcessor(
        session_factory=Factory(),
        encryption=Encryption(),
        portal_oauth=oauth,
        settings=Settings(),
        clock=lambda: resumed,
        random_source=lambda: 0,
    )

    async def load(_):
        return job, portal

    monkeypatch.setattr(processor, "_load", load)
    monkeypatch.setattr("app.jobs.processor.AutomationJobRepository", Repository)
    await processor.process(job_id)
    assert oauth.calls[0][1] == "bizproc.event.send"
    params = oauth.calls[0][2]
    assert set(params) == {"EVENT_TOKEN", "RETURN_VALUES", "LOG_MESSAGE"}
    assert params["EVENT_TOKEN"] == "test-event-token"
    assert set(params["RETURN_VALUES"]) == {
        "status",
        "job_id",
        "scheduled_at",
        "resumed_at",
        "requested_delay_seconds",
        "actual_delay_seconds",
    }
    assert params["RETURN_VALUES"]["actual_delay_seconds"] == 11
    assert "encrypted-test-value" not in str(params)
    assert Repository.completed[1]["return_values"] == params["RETURN_VALUES"]


class TransitionRepository(Repository):
    transition = None

    async def mark_failed(self, job_id, *, error_code):
        TransitionRepository.transition = ("failed", error_code)
        return True

    async def mark_expired(self, job_id, *, error_code):
        TransitionRepository.transition = ("expired", error_code)
        return True

    async def schedule_retry(self, job_id, *, run_at, error_code):
        TransitionRepository.transition = ("retry", error_code)
        return True


async def test_access_denied_expires_event_and_transport_retries(monkeypatch):
    from app.bitrix.exceptions import BitrixPermissionError, BitrixTransportError

    scheduled = datetime(2026, 7, 27, 12, tzinfo=UTC)
    job = SimpleNamespace(
        id=uuid4(),
        portal_id=uuid4(),
        status="processing",
        robot_code="sinedis.short_pause.v1",
        event_token_encrypted="fernet:v1:encrypted-test-value",
        payload={"requested_delay_seconds": 10, "scheduled_at": scheduled.isoformat()},
        last_error=None,
        attempts=1,
        max_attempts=10,
    )
    portal = SimpleNamespace(id=job.portal_id, status="active")

    class ErrorOAuth:
        def __init__(self, error):
            self.error = error

        async def call_portal(self, *args):
            raise self.error

    async def load(_):
        return job, portal

    monkeypatch.setattr("app.jobs.processor.AutomationJobRepository", TransitionRepository)
    permission = BitrixPermissionError(
        code="ACCESS_DENIED", http_status=403, method="bizproc.event.send", retryable=False
    )
    processor = AutomationJobProcessor(
        session_factory=Factory(),
        encryption=Encryption(),
        portal_oauth=ErrorOAuth(permission),
        settings=Settings(),
        clock=lambda: scheduled,
        random_source=lambda: 0,
    )
    monkeypatch.setattr(processor, "_load", load)
    await processor.process(job.id)
    assert TransitionRepository.transition == ("expired", "event_token_invalid_or_expired")
    processor = AutomationJobProcessor(
        session_factory=Factory(),
        encryption=Encryption(),
        portal_oauth=ErrorOAuth(BitrixTransportError(method="bizproc.event.send")),
        settings=Settings(),
        clock=lambda: scheduled,
        random_source=lambda: 0,
    )
    monkeypatch.setattr(processor, "_load", load)
    await processor.process(job.id)
    assert TransitionRepository.transition == ("retry", "event_delivery_outcome_unknown")


async def test_access_denied_after_ambiguous_delivery_requires_manual_review(monkeypatch):
    from app.bitrix.exceptions import BitrixPermissionError

    scheduled = datetime(2026, 7, 27, 12, tzinfo=UTC)
    job = SimpleNamespace(
        id=uuid4(),
        portal_id=uuid4(),
        status="processing",
        robot_code="sinedis.short_pause.v1",
        event_token_encrypted="fernet:v1:encrypted-test-value",
        payload={"requested_delay_seconds": 10, "scheduled_at": scheduled.isoformat()},
        last_error="event_delivery_outcome_unknown",
        attempts=2,
        max_attempts=10,
    )
    portal = SimpleNamespace(id=job.portal_id, status="active")
    error = BitrixPermissionError(
        code="ACCESS_DENIED", http_status=403, method="bizproc.event.send", retryable=False
    )

    class OAuthError:
        async def call_portal(self, *args):
            raise error

    processor = AutomationJobProcessor(
        session_factory=Factory(),
        encryption=Encryption(),
        portal_oauth=OAuthError(),
        settings=Settings(),
        clock=lambda: scheduled,
    )

    async def load(_):
        return job, portal

    monkeypatch.setattr(processor, "_load", load)
    monkeypatch.setattr("app.jobs.processor.AutomationJobRepository", TransitionRepository)
    await processor.process(job.id)
    assert TransitionRepository.transition == ("failed", "event_delivery_requires_manual_review")
