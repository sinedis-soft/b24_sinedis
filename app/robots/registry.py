"""Extensible in-process registry of application robot definitions."""

from app.robots.base import RobotDefinition
from app.robots.rest_request import REST_REQUEST_ACTIVITY, REST_REQUEST_ROBOT
from app.robots.short_pause import SHORT_PAUSE_ROBOT
from app.robots.wait_field import WAIT_FIELD_ACTIVITY, WAIT_FIELD_ROBOT


class RobotRegistry:
    """Read-only registry without database or network responsibilities."""

    def __init__(self, definitions: tuple[RobotDefinition, ...]) -> None:
        codes = [definition.code for definition in definitions]
        if len(codes) != len(set(codes)):
            raise ValueError("Robot codes must be unique")
        self._definitions = definitions

    def all(self) -> tuple[RobotDefinition, ...]:
        return self._definitions

    def get(self, code: str) -> RobotDefinition:
        for definition in self._definitions:
            if definition.code == code:
                return definition
        raise KeyError(code)


robot_registry = RobotRegistry((SHORT_PAUSE_ROBOT, REST_REQUEST_ROBOT, WAIT_FIELD_ROBOT))
activity_registry = RobotRegistry((REST_REQUEST_ACTIVITY, WAIT_FIELD_ACTIVITY))
