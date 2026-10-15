"""Authenticated field configuration, server-side launches, and audit inspection."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bitrix.client import BitrixClient
from app.bitrix.exceptions import BitrixError
from app.bitrix.oauth import BitrixOAuthService, PortalOAuthService
from app.bitrix.workflow_fields import USER_TYPE_ID
from app.config import get_settings
from app.database import get_db_session, get_session_factory
from app.models.portal import BitrixPortal, PortalStatus
from app.models.workflow_button import WorkflowButton, WorkflowLaunch
from app.security.encryption import get_encryption_service


class SafeButtonRoute(APIRoute):
    """Do not echo credential-bearing inputs in validation errors."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except RequestValidationError as exc:
                raise HTTPException(422, "Некорректные параметры запроса") from exc

        return handle


router = APIRouter(prefix="/api/buttons", tags=["workflow-buttons"], route_class=SafeButtonRoute)
WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


class Credentials(BaseModel):
    member_id: str = Field(min_length=1, max_length=64)
    access_token: SecretStr = Field(min_length=1, max_length=4096)


class FieldRequest(Credentials):
    field_name: str = Field(pattern=r"^UF_CRM_[A-Z0-9_]{1,90}$")


class ButtonSettings(BaseModel):
    label: str = Field(default="Запустить бизнес-процесс", min_length=1, max_length=100)
    template_id: int = Field(gt=0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    allowed_user_ids: list[int] = Field(default_factory=list, max_length=1000)
    color: str = Field(default="#2563eb", pattern=r"^#[0-9a-fA-F]{6}$")
    width: int = Field(default=240, ge=80, le=800)
    height: int = Field(default=40, ge=24, le=80)
    enabled: bool = False
    confirm: bool = True
    cooldown_seconds: int = Field(default=30, ge=10, le=86400)
    block_until_released: bool = False

    @field_validator("parameters")
    @classmethod
    def bounded_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value)) > 100_000:
            raise ValueError("Parameters exceed 100000 characters")
        return value

    @field_validator("allowed_user_ids")
    @classmethod
    def positive_users(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("User IDs must be positive")
        return sorted(set(values))


class SaveRequest(FieldRequest):
    settings: ButtonSettings


class LaunchRequest(FieldRequest):
    deal_id: int = Field(gt=0)
    request_id: UUID


class ReleaseRequest(Credentials):
    launch_id: UUID


@dataclass
class Actor:
    portal: BitrixPortal
    user_id: int
    admin: bool
    client: BitrixClient
    server: PortalOAuthService


def forbidden() -> HTTPException:
    return HTTPException(403, "Нет разрешения на действие")


async def actor(
    body: Credentials,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AsyncIterator[Actor]:
    """Use only a stored endpoint; prove app identity and user through Bitrix REST."""
    if not get_settings().workflow_buttons_enabled:
        raise HTTPException(404, "Механизм кнопок выключен")
    portal = await session.scalar(
        select(BitrixPortal).where(
            BitrixPortal.member_id == body.member_id,
            BitrixPortal.status == PortalStatus.ACTIVE.value,
        )
    )
    if portal is None:
        raise forbidden()
    async with (
        BitrixOAuthService() as oauth,
        BitrixClient(
            client_endpoint=portal.client_endpoint,
            access_token=body.access_token.get_secret_value(),
        ) as client,
    ):
        server = PortalOAuthService(
            session_factory=get_session_factory(),
            encryption=get_encryption_service(),
            oauth=oauth,
        )
        try:
            info = (await client.call("app.info")).result
            installed = (await server.call_portal(portal.id, "app.info")).result
            if not info.get("ID") or str(info["ID"]) != str(installed.get("ID")):
                raise forbidden()
            profile = (await client.call("profile")).result
            admin = (await client.call("user.admin")).result is True
            user_id = int(profile["ID"])
            if user_id <= 0:
                raise forbidden()
        except (BitrixError, KeyError, ValueError, TypeError, AttributeError) as exc:
            raise forbidden() from exc
        yield Actor(portal, user_id, admin, client, server)


ActorDep = Annotated[Actor, Depends(actor)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def can_launch(settings: ButtonSettings, who: Actor) -> bool:
    # Empty allowlist deliberately means administrators only.
    return settings.enabled and (who.admin or who.user_id in settings.allowed_user_ids)


async def validate_field(who: Actor, field_name: str) -> None:
    """Reject standard or foreign app fields even when an administrator supplies a code."""
    response = await who.server.call_portal(
        who.portal.id, "crm.deal.userfield.list", {"filter": {"FIELD_NAME": field_name}}
    )
    info = (await who.server.call_portal(who.portal.id, "app.info")).result
    expected = f"rest_{info['ID']}_{USER_TYPE_ID}"
    fields = response.result
    if isinstance(fields, list):
        for item in fields:
            if not isinstance(item, dict) or item.get("FIELD_NAME") != field_name:
                continue
            actual = item.get("USER_TYPE_ID")
            if actual == expected:
                return
            if actual == USER_TYPE_ID:
                # CRM may return the short code instead of rest_<app_id>_<code>.
                # Prove ownership through the current app's registry before accepting it.
                registered = (
                    await who.server.call_portal(who.portal.id, "userfieldtype.list")
                ).result
                if isinstance(registered, list) and any(
                    isinstance(entry, dict) and entry.get("USER_TYPE_ID") == USER_TYPE_ID
                    for entry in registered
                ):
                    return
    raise HTTPException(400, "Поле не принадлежит типу кнопки SINEDIS")


async def button_for(session: AsyncSession, who: Actor, name: str) -> WorkflowButton | None:
    return await session.scalar(
        select(WorkflowButton).where(
            WorkflowButton.portal_id == who.portal.id, WorkflowButton.field_name == name
        )
    )


@router.api_route("/field", methods=["GET", "POST"], response_class=HTMLResponse)
async def field_page() -> HTMLResponse:
    return page("field.html")


@router.api_route("/settings", methods=["GET", "POST"], response_class=HTMLResponse)
async def settings_page() -> HTMLResponse:
    return page("settings.html")


def page(filename: str) -> HTMLResponse:
    if not get_settings().workflow_buttons_enabled:
        raise HTTPException(404, "Механизм кнопок выключен")
    return HTMLResponse(
        (WEB_ROOT / filename).read_text(),
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/{asset}.js")
async def javascript(asset: str) -> Response:
    if asset not in {"field", "settings", "ui"} or not get_settings().workflow_buttons_enabled:
        raise HTTPException(404)
    return Response((WEB_ROOT / f"{asset}.js").read_text(), media_type="text/javascript")


@router.get("/ui.css")
async def stylesheet() -> Response:
    if not get_settings().workflow_buttons_enabled:
        raise HTTPException(404)
    return Response((WEB_ROOT / "ui.css").read_text(), media_type="text/css")


@router.post("/config")
async def config(body: FieldRequest, who: ActorDep, session: SessionDep) -> dict:
    button = await button_for(session, who, body.field_name)
    if button is None:
        return {"configured": False, "admin": who.admin}
    settings = ButtonSettings.model_validate(button.settings)
    if not who.admin and not can_launch(settings, who):
        raise forbidden()
    result = (
        settings.model_dump()
        if who.admin
        else {
            key: getattr(settings, key) for key in ("label", "color", "width", "height", "confirm")
        }
    )
    return {
        "configured": True,
        "admin": who.admin,
        "can_launch": can_launch(settings, who),
        "settings": result,
    }


@router.post("/save")
async def save(body: SaveRequest, who: ActorDep, session: SessionDep) -> dict:
    if not who.admin:
        raise forbidden()
    try:
        await validate_field(who, body.field_name)
    except BitrixError as exc:
        raise HTTPException(502, "Проверка поля не выполнена") from exc
    # Lock the portal to serialize creation as well as edits of this field's config.
    await session.scalar(
        select(BitrixPortal).where(BitrixPortal.id == who.portal.id).with_for_update()
    )
    button = await button_for(session, who, body.field_name)
    if button is None:
        button = WorkflowButton(portal_id=who.portal.id, field_name=body.field_name)
        session.add(button)
    button.settings = body.settings.model_dump()
    await session.commit()
    return {"ok": True}


def launch_result(launch: WorkflowLaunch) -> dict:
    return {"launch_id": str(launch.id), "status": launch.status, "workflow_id": launch.workflow_id}


@router.post("/launch")
async def launch(body: LaunchRequest, who: ActorDep, session: SessionDep) -> dict:
    try:
        # Check deal read permission using the caller token, never the admin token.
        await who.client.call("crm.deal.get", {"id": body.deal_id})
    except BitrixError as exc:
        raise forbidden() from exc
    await session.scalar(
        select(BitrixPortal).where(BitrixPortal.id == who.portal.id).with_for_update()
    )
    button = await session.scalar(
        select(WorkflowButton)
        .where(
            WorkflowButton.portal_id == who.portal.id,
            WorkflowButton.field_name == body.field_name,
        )
        .with_for_update()
    )
    if button is None:
        raise HTTPException(404, "Кнопка не настроена")
    settings = ButtonSettings.model_validate(button.settings)
    if not can_launch(settings, who):
        raise forbidden()
    previous = await session.scalar(
        select(WorkflowLaunch).where(
            WorkflowLaunch.portal_id == who.portal.id, WorkflowLaunch.request_id == body.request_id
        )
    )
    if previous is not None:
        if (previous.button_id, previous.deal_id, previous.user_id) != (
            button.id,
            body.deal_id,
            who.user_id,
        ):
            raise HTTPException(409, "Идентификатор запроса уже использован")
        return launch_result(previous)
    recent = await session.scalar(
        select(WorkflowLaunch)
        .where(WorkflowLaunch.button_id == button.id, WorkflowLaunch.deal_id == body.deal_id)
        .order_by(WorkflowLaunch.created_at.desc())
        .limit(1)
    )
    now = datetime.now(UTC)
    if recent is not None and (
        (recent.released_at is None and recent.status in {"starting", "unknown", "blocked"})
        or recent.created_at > now - timedelta(seconds=settings.cooldown_seconds)
    ):
        raise HTTPException(409, "Повторный запуск заблокирован; проверьте журнал запусков")
    record = WorkflowLaunch(
        portal_id=who.portal.id,
        button_id=button.id,
        request_id=body.request_id,
        deal_id=body.deal_id,
        user_id=who.user_id,
        template_id=settings.template_id,
        status="starting",
    )
    session.add(record)
    # Reservation survives a crash or timeout; do not resend uncertain launches.
    await session.commit()
    try:
        response = await who.server.call_portal(
            who.portal.id,
            "bizproc.workflow.start",
            {
                "TEMPLATE_ID": settings.template_id,
                "DOCUMENT_ID": ["crm", "CCrmDocumentDeal", f"DEAL_{body.deal_id}"],
                "PARAMETERS": settings.parameters,
            },
        )
        if (
            not isinstance(response.result, str)
            or not response.result
            or len(response.result) > 100
        ):
            record.status = "unknown"
        else:
            record.workflow_id = response.result
            record.status = "blocked" if settings.block_until_released else "started"
    except BitrixError:
        # Even a 5xx can be received after Bitrix has created the workflow.
        record.status = "unknown"
    await session.commit()
    return launch_result(record)


@router.post("/journal")
async def journal(body: FieldRequest, who: ActorDep, session: SessionDep) -> dict:
    if not who.admin:
        raise forbidden()
    button = await button_for(session, who, body.field_name)
    if button is None:
        return {"launches": []}
    records = (
        await session.scalars(
            select(WorkflowLaunch)
            .where(WorkflowLaunch.button_id == button.id)
            .order_by(WorkflowLaunch.created_at.desc())
            .limit(50)
        )
    ).all()
    return {
        "launches": [
            {
                **launch_result(row),
                "deal_id": row.deal_id,
                "user_id": row.user_id,
                "template_id": row.template_id,
                "created_at": row.created_at.isoformat(),
                "released_by": row.released_by,
            }
            for row in records
        ]
    }


@router.post("/release")
async def release(body: ReleaseRequest, who: ActorDep, session: SessionDep) -> dict:
    if not who.admin:
        raise forbidden()
    record = await session.scalar(
        select(WorkflowLaunch)
        .where(WorkflowLaunch.id == body.launch_id, WorkflowLaunch.portal_id == who.portal.id)
        .with_for_update()
    )
    if record is None:
        raise HTTPException(404, "Запуск не найден")
    # A request in flight must not be unlocked while its HTTP operation may still finish.
    if record.created_at > datetime.now(UTC) - timedelta(minutes=5):
        raise HTTPException(409, "Разблокировка доступна через пять минут после запуска")
    record.released_at = datetime.now(UTC)
    record.released_by = who.user_id
    await session.commit()
    return {"ok": True}
