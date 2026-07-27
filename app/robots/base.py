"""Typed, database-independent robot definitions."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RobotDefinition:
    """Application-owned Bitrix24 robot metadata."""

    code: str
    handler_path: str
    name: Mapping[str, str]
    description: Mapping[str, str]
    properties: Mapping[str, Mapping[str, Any]]
    return_properties: Mapping[str, Mapping[str, Any]]
    use_subscription: bool = True
    use_placement: bool = False

    def fields(self, handler_url: str) -> dict[str, Any]:
        """Build the FIELDS contract accepted by robot add/update methods."""
        return {
            "HANDLER": handler_url,
            "NAME": dict(self.name),
            "DESCRIPTION": dict(self.description),
            "PROPERTIES": {key: dict(value) for key, value in self.properties.items()},
            "RETURN_PROPERTIES": {
                key: dict(value) for key, value in self.return_properties.items()
            },
            "USE_SUBSCRIPTION": "Y" if self.use_subscription else "N",
            "USE_PLACEMENT": "Y" if self.use_placement else "N",
        }

    def add_payload(self, handler_url: str) -> dict[str, Any]:
        """Build a complete bizproc.robot.add request."""
        return {"CODE": self.code, **self.fields(handler_url)}
