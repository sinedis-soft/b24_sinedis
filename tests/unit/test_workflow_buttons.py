"""Security boundaries and non-retry launch behavior without a live portal."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.workflow_buttons import (
    Actor,
    ButtonSettings,
    FieldRequest,
    LaunchRequest,
    ReleaseRequest,
    SaveRequest,
    can_launch,
    config,
    launch,
    release,
    save,
    validate_field,
)
from app.api.workflow_buttons import (
    actor as authenticate_actor,
)
from app.bitrix.exceptions import BitrixTransportError
from app.bitrix.workflow_fields import USER_TYPE_ID, ensure_workflow_field_type
from app.database import get_db_session
from app.main import create_app
from app.models.workflow_button import WorkflowButton, WorkflowLaunch


def who(admin=False, user_id=17):
    return Actor(
        SimpleNamespace(id=uuid4(), client_endpoint="https://portal.test/rest/"),
        user_id,
        admin,
        AsyncMock(),
        AsyncMock(),
    )


def request(**kwargs):
    return LaunchRequest(
        member_id="portal",
        access_token="secret",
        field_name="UF_CRM_BUTTON",
        deal_id=42,
        request_id=uuid4(),
        **kwargs,
    )


def button(actor, **kwargs):
    settings = ButtonSettings(template_id=125, enabled=True, allowed_user_ids=[17], **kwargs)
    return WorkflowButton(
        id=uuid4(),
        portal_id=actor.portal.id,
        field_name="UF_CRM_BUTTON",
        settings=settings.model_dump(),
    )


def db(*values):
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=values)
    session.commit = AsyncMock()
    return session


def test_allowlist_is_closed_by_default():
    settings = ButtonSettings(template_id=125, enabled=True)
    assert not can_launch(settings, who())
    assert can_launch(settings, who(admin=True))
    assert not can_launch(settings.model_copy(update={"enabled": False}), who(admin=True))
    assert can_launch(settings.model_copy(update={"allowed_user_ids": [17]}), who())


@pytest.mark.parametrize(
    "change",
    [
        {"color": "red;url(x)"},
        {"height": 1000},
        {"allowed_user_ids": [-1]},
        {"parameters": {"x": "a" * 100001}},
    ],
)
def test_settings_reject_unsafe_or_unbounded_values(change):
    with pytest.raises(ValidationError):
        ButtonSettings(template_id=125, **change)


async def test_duplicate_key_returns_existing_launch_without_calling_bitrix():
    actor = who()
    body = request()
    item = button(actor)
    existing = WorkflowLaunch(
        id=uuid4(),
        button_id=item.id,
        deal_id=42,
        user_id=17,
        status="started",
        workflow_id="workflow",
        request_id=body.request_id,
    )
    result = await launch(body, actor, db(actor.portal, item, existing))
    assert result["workflow_id"] == "workflow"
    actor.server.call_portal.assert_not_called()


async def test_uuid_reuse_for_another_deal_is_rejected():
    actor = who()
    item = button(actor)
    existing = SimpleNamespace(button_id=item.id, deal_id=99, user_id=17)
    with pytest.raises(HTTPException) as error:
        await launch(request(), actor, db(actor.portal, item, existing))
    assert error.value.status_code == 409
    actor.server.call_portal.assert_not_called()


@pytest.mark.parametrize("status", ["starting", "unknown", "blocked"])
async def test_unresolved_launch_blocks_even_after_cooldown(status):
    actor = who()
    recent = SimpleNamespace(
        status=status, released_at=None, created_at=datetime.now(UTC) - timedelta(days=1)
    )
    with pytest.raises(HTTPException) as error:
        await launch(request(), actor, db(actor.portal, button(actor), None, recent))
    assert error.value.status_code == 409
    actor.server.call_portal.assert_not_called()


async def test_permission_failure_never_reaches_admin_launch():
    actor = who()
    actor.client.call.side_effect = BitrixTransportError(method="crm.deal.get")
    with pytest.raises(HTTPException) as error:
        await launch(request(), actor, db())
    assert error.value.status_code == 403
    actor.server.call_portal.assert_not_called()


async def test_failed_allowlist_never_reaches_admin_launch():
    actor = who(user_id=18)
    with pytest.raises(HTTPException) as error:
        await launch(request(), actor, db(actor.portal, button(actor)))
    assert error.value.status_code == 403
    actor.server.call_portal.assert_not_called()


async def test_reservation_committed_before_remote_launch_and_timeout_not_retried():
    actor = who()
    session = db(actor.portal, button(actor), None, None)
    committed = []
    session.commit.side_effect = lambda: committed.append(True)

    async def start(*args):
        assert committed == [True]
        raise BitrixTransportError(method="bizproc.workflow.start")

    actor.server.call_portal.side_effect = start
    result = await launch(request(), actor, session)
    assert result["status"] == "unknown"
    actor.server.call_portal.assert_awaited_once()
    record = session.add.call_args.args[0]
    assert record.user_id == 17 and record.template_id == 125
    assert not hasattr(record, "access_token")


async def test_success_uses_saved_template_and_parameters():
    actor = who()
    item = button(actor, parameters={"source": "button"}, block_until_released=True)
    actor.server.call_portal.return_value = SimpleNamespace(result="workflow-id")
    result = await launch(request(), actor, db(actor.portal, item, None, None))
    assert result["status"] == "blocked"
    actor.server.call_portal.assert_awaited_once_with(
        actor.portal.id,
        "bizproc.workflow.start",
        {
            "TEMPLATE_ID": 125,
            "DOCUMENT_ID": ["crm", "CCrmDocumentDeal", "DEAL_42"],
            "PARAMETERS": {"source": "button"},
        },
    )


async def test_employee_cannot_save_or_release():
    actor = who()
    with pytest.raises(HTTPException):
        await save(
            SaveRequest(**request().model_dump(), settings=ButtonSettings(template_id=125)),
            actor,
            db(),
        )
    with pytest.raises(HTTPException):
        await release(
            ReleaseRequest(member_id="portal", access_token="secret", launch_id=uuid4()),
            actor,
            db(),
        )


async def test_foreign_field_type_is_rejected():
    actor = who(admin=True)
    actor.server.call_portal.side_effect = [
        SimpleNamespace(result=[{"FIELD_NAME": "UF_CRM_BUTTON", "USER_TYPE_ID": "string"}]),
        SimpleNamespace(result={"ID": 123}),
    ]
    with pytest.raises(HTTPException):
        await validate_field(actor, "UF_CRM_BUTTON")


async def test_registration_updates_existing_type():
    client = AsyncMock()
    client.call.return_value = SimpleNamespace(result=[{"USER_TYPE_ID": USER_TYPE_ID}])
    await ensure_workflow_field_type(client, "https://app.test/api/buttons/field")
    assert client.call.call_args.args[0] == "userfieldtype.update"


async def test_config_does_not_disclose_template_to_employee():
    actor = who()
    result = await config(FieldRequest(**request().model_dump()), actor, db(button(actor)))
    assert "template_id" not in result["settings"]
    assert "parameters" not in result["settings"]


def test_http_body_is_flat_and_validation_does_not_echo_credentials(monkeypatch):
    monkeypatch.setenv("WORKFLOW_BUTTONS_ENABLED", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    application = create_app()
    actor = who()
    session = db(button(actor))
    application.dependency_overrides[authenticate_actor] = lambda: actor
    application.dependency_overrides[get_db_session] = lambda: session
    try:
        with TestClient(application) as client:
            response = client.post(
                "/api/buttons/config",
                json={
                    "member_id": "portal",
                    "access_token": "secret",
                    "field_name": "UF_CRM_BUTTON",
                },
            )
            assert response.status_code == 200, response.text
            assert "secret" not in response.text
            response = client.post(
                "/api/buttons/config",
                json={"member_id": "portal", "access_token": "secret", "field_name": "invalid"},
            )
            assert response.status_code == 422
            assert "secret" not in response.text
    finally:
        get_settings.cache_clear()


async def test_authentication_rejects_token_for_another_application(monkeypatch):
    from contextlib import asynccontextmanager

    from app.api.workflow_buttons import Credentials
    from app.config import Settings

    client = AsyncMock()
    client.call.return_value = SimpleNamespace(result={"ID": 999})
    server = AsyncMock()
    server.call_portal.return_value = SimpleNamespace(result={"ID": 123})

    @asynccontextmanager
    async def fake_client(**kwargs):
        assert kwargs["client_endpoint"] == "https://portal.test/rest/"
        yield client

    @asynccontextmanager
    async def fake_oauth():
        yield object()

    monkeypatch.setattr(
        "app.api.workflow_buttons.get_settings", lambda: Settings(workflow_buttons_enabled=True)
    )
    monkeypatch.setattr("app.api.workflow_buttons.BitrixClient", fake_client)
    monkeypatch.setattr("app.api.workflow_buttons.BitrixOAuthService", fake_oauth)
    monkeypatch.setattr("app.api.workflow_buttons.PortalOAuthService", lambda **kwargs: server)
    monkeypatch.setattr("app.api.workflow_buttons.get_encryption_service", lambda: object())
    monkeypatch.setattr("app.api.workflow_buttons.get_session_factory", lambda: object())
    generator = authenticate_actor(
        Credentials(member_id="portal", access_token="secret"), db(who().portal)
    )
    with pytest.raises(HTTPException) as error:
        await anext(generator)
    assert error.value.status_code == 403
    assert client.call.await_count == 1


async def test_release_checks_portal_boundary_and_inflight_age():
    admin = who(admin=True)
    body = ReleaseRequest(member_id="portal", access_token="secret", launch_id=uuid4())
    with pytest.raises(HTTPException) as error:
        await release(body, admin, db(None))
    assert error.value.status_code == 404
    record = SimpleNamespace(created_at=datetime.now(UTC))
    with pytest.raises(HTTPException) as error:
        await release(body, admin, db(record))
    assert error.value.status_code == 409
    record.created_at -= timedelta(minutes=6)
    session = db(record)
    assert await release(body, admin, session) == {"ok": True}
    assert record.released_by == admin.user_id
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("field_type", ["rest_293_sinedis_bp_button", "sinedis_bp_button"])
async def test_accepts_crm_full_and_short_field_type_codes(field_type):
    actor = who(admin=True)
    actor.server.call_portal.side_effect = [
        SimpleNamespace(result=[{"FIELD_NAME": "UF_CRM_BUTTON", "USER_TYPE_ID": field_type}]),
        SimpleNamespace(result={"ID": 293}),
        SimpleNamespace(result=[{"USER_TYPE_ID": "sinedis_bp_button"}]),
    ]
    await validate_field(actor, "UF_CRM_BUTTON")
    if field_type == "sinedis_bp_button":
        assert actor.server.call_portal.call_args.args == (actor.portal.id, "userfieldtype.list")


@pytest.mark.parametrize("registry", [[], [{"USER_TYPE_ID": "other_type"}], None, {}])
async def test_short_code_requires_registration_owned_by_current_app(registry):
    actor = who(admin=True)
    actor.server.call_portal.side_effect = [
        SimpleNamespace(
            result=[{"FIELD_NAME": "UF_CRM_BUTTON", "USER_TYPE_ID": "sinedis_bp_button"}]
        ),
        SimpleNamespace(result={"ID": 293}),
        SimpleNamespace(result=registry),
    ]
    with pytest.raises(HTTPException) as error:
        await validate_field(actor, "UF_CRM_BUTTON")
    assert error.value.status_code == 400


@pytest.mark.parametrize(
    "fields",
    [
        [{"FIELD_NAME": "UF_CRM_BUTTON", "USER_TYPE_ID": "rest_999_sinedis_bp_button"}],
        [{"FIELD_NAME": "UF_CRM_OTHER", "USER_TYPE_ID": "rest_293_sinedis_bp_button"}],
        [{"FIELD_NAME": "UF_CRM_BUTTON", "USER_TYPE_ID": "string"}],
        [],
        None,
        {},
        [None],
    ],
)
async def test_rejects_other_app_other_field_and_malformed_crm_results(fields):
    actor = who(admin=True)
    actor.server.call_portal.side_effect = [
        SimpleNamespace(result=fields),
        SimpleNamespace(result={"ID": 293}),
    ]
    with pytest.raises(HTTPException) as error:
        await validate_field(actor, "UF_CRM_BUTTON")
    assert error.value.status_code == 400
    assert actor.server.call_portal.await_count == 2
