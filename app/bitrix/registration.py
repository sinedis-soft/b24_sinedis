"""Idempotent registration of robots and the application uninstall event."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.bitrix.client import BitrixClient
from app.bitrix.exceptions import BitrixConfigurationError
from app.config import Settings, get_settings
from app.robots.registry import RobotRegistry, activity_registry, robot_registry

UNINSTALL_EVENT = "ONAPPUNINSTALL"


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """Safe statuses returned by idempotent registration operations."""

    robots: Mapping[str, str]
    events: Mapping[str, str]
    activities: Mapping[str, str] = field(default_factory=dict)


def normalize_app_base_url(value: str, *, environment: str) -> str:
    """Validate the externally reachable application origin/base path."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except (TypeError, ValueError) as exc:
        raise BitrixConfigurationError("APP_BASE_URL is invalid") from exc
    local_development = environment == "development" and hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    if (
        hostname is None
        or parsed.scheme.lower() not in {"http", "https"}
        or (parsed.scheme.lower() != "https" and not local_development)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in value)
    ):
        raise BitrixConfigurationError("APP_BASE_URL is invalid")
    path = parsed.path.rstrip("/") + "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


class BitrixRegistrationService:
    """Keep externally registered application metadata synchronized."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        registry: RobotRegistry = robot_registry,
        activities: RobotRegistry = activity_registry,
    ) -> None:
        self._settings = settings or get_settings()
        self._base_url = normalize_app_base_url(
            self._settings.app_base_url, environment=self._settings.app_env
        )
        self._registry = registry
        self._activities = activities

    def handler_url(self, path: str) -> str:
        """Build a handler from validated APP_BASE_URL and a local fixed path."""
        return f"{self._base_url}{path.lstrip('/')}"

    async def ensure_robots_registered(self, client: BitrixClient) -> Mapping[str, str]:
        """Add missing robots and update existing robots without deleting anything."""
        listed = await client.call("bizproc.robot.list", {})
        existing_codes = _robot_codes(listed.result)
        statuses: dict[str, str] = {}
        for definition in self._registry.all():
            handler = self.handler_url(definition.handler_path)
            if definition.code in existing_codes:
                await client.call(
                    "bizproc.robot.update",
                    {"CODE": definition.code, "FIELDS": definition.fields(handler)},
                )
                statuses[definition.code] = "updated"
            else:
                await client.call("bizproc.robot.add", definition.add_payload(handler))
                statuses[definition.code] = "added"
        return statuses

    async def ensure_uninstall_event_registered(self, client: BitrixClient) -> Mapping[str, str]:
        """Bind the exact uninstall handler only when event.get does not contain it."""
        handler = self.handler_url("api/bitrix/uninstall")
        listed = await client.call("event.get", {})
        if _has_event_handler(listed.result, event=UNINSTALL_EVENT, handler=handler):
            return {UNINSTALL_EVENT: "already_bound"}
        await client.call("event.bind", {"event": UNINSTALL_EVENT, "handler": handler})
        return {UNINSTALL_EVENT: "bound"}

    async def ensure_activities_registered(self, client: BitrixClient) -> Mapping[str, str]:
        """Idempotently add or update classic workflow activities."""
        listed = await client.call("bizproc.activity.list", {})
        existing_codes = _robot_codes(listed.result)
        statuses: dict[str, str] = {}
        for definition in self._activities.all():
            handler = self.handler_url(definition.handler_path)
            if definition.code in existing_codes:
                await client.call(
                    "bizproc.activity.update",
                    {"CODE": definition.code, "FIELDS": definition.fields(handler)},
                )
                statuses[definition.code] = "updated"
            else:
                await client.call("bizproc.activity.add", definition.add_payload(handler))
                statuses[definition.code] = "added"
        return statuses

    async def ensure_all(self, client: BitrixClient) -> RegistrationResult:
        robots = await self.ensure_robots_registered(client)
        activities = await self.ensure_activities_registered(client)
        events = await self.ensure_uninstall_event_registered(client)
        return RegistrationResult(robots=robots, activities=activities, events=events)


def _items(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        candidates = value.get("result", value.get("events", value.get("robots", value)))
        if isinstance(candidates, list):
            return [item for item in candidates if isinstance(item, Mapping)]
        if all(isinstance(item, Mapping) for item in candidates.values()):
            return list(candidates.values())
    return []


def _robot_codes(value: Any) -> set[str]:
    result: set[str] = set()
    for item in _items(value):
        code = item.get("CODE", item.get("code"))
        if isinstance(code, str):
            result.add(code)
    return result


def _has_event_handler(value: Any, *, event: str, handler: str) -> bool:
    for item in _items(value):
        registered_event = item.get("event", item.get("EVENT"))
        registered_handler = item.get("handler", item.get("HANDLER"))
        if registered_event == event and registered_handler == handler:
            return True
    return False
