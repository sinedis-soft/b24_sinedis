"""Robot definition, registry, registration, and callback payload tests."""

import pytest

from app.bitrix.client import BitrixResponse
from app.bitrix.exceptions import BitrixConfigurationError
from app.bitrix.registration import BitrixRegistrationService
from app.config import Settings
from app.robots.payload import RobotPayloadError, normalize_robot_payload
from app.robots.short_pause import SHORT_PAUSE_CODE, SHORT_PAUSE_ROBOT


class FakeClient:
    def __init__(self, robots=None, events=None):
        self.robots, self.events, self.calls = robots or [], events or [], []

    async def call(self, method, params=None):
        self.calls.append((method, params))
        result = (
            self.robots
            if method == "bizproc.robot.list"
            else self.events
            if method == "event.get"
            else True
        )
        return BitrixResponse(result=result)


def settings(url="https://app.test/"):
    return Settings(APP_BASE_URL=url)


def test_short_pause_definition_is_subscription_robot():
    fields = SHORT_PAUSE_ROBOT.fields("https://app.test/api/bitrix/robots/short-pause")
    assert SHORT_PAUSE_ROBOT.code == SHORT_PAUSE_CODE
    assert fields["USE_SUBSCRIPTION"] == "Y" and fields["USE_PLACEMENT"] == "N"
    assert fields["PROPERTIES"]["delay_seconds"]["Type"] == "int"
    assert fields["PROPERTIES"]["delay_seconds"]["Required"] == "Y"
    assert fields["PROPERTIES"]["delay_seconds"]["Default"] == 10
    assert fields["PROPERTIES"]["comment"]["Required"] == "N"
    assert set(fields["RETURN_PROPERTIES"]) == {
        "status",
        "job_id",
        "scheduled_at",
        "resumed_at",
        "requested_delay_seconds",
        "actual_delay_seconds",
    }
    assert not ({"DOCUMENT_TYPE", "FILTER", "AUTH_USER_ID"} & fields.keys())


async def test_registration_adds_robot_and_binds_event():
    client = FakeClient()
    result = await BitrixRegistrationService(settings=settings()).ensure_all(client)
    assert result.robots[SHORT_PAUSE_CODE] == "added"
    assert result.events["ONAPPUNINSTALL"] == "bound"
    assert client.calls[1][0] == "bizproc.robot.add"
    assert client.calls[-1] == (
        "event.bind",
        {"event": "ONAPPUNINSTALL", "handler": "https://app.test/api/bitrix/uninstall"},
    )


async def test_registration_updates_and_does_not_rebind():
    handler = "https://app.test/api/bitrix/uninstall"
    client = FakeClient(
        [{"CODE": SHORT_PAUSE_CODE}, {"CODE": "unrelated"}],
        [{"event": "ONAPPUNINSTALL", "handler": handler}],
    )
    result = await BitrixRegistrationService(settings=settings()).ensure_all(client)
    update = next(call for call in client.calls if call[0] == "bizproc.robot.update")
    assert set(update[1]) == {"CODE", "FIELDS"}
    assert "CODE" not in update[1]["FIELDS"]
    assert result.events["ONAPPUNINSTALL"] == "already_bound"
    assert not any(method == "event.bind" for method, _ in client.calls)


@pytest.mark.parametrize(
    "url",
    [
        "http://app.test",
        "https://user:password@app.test",
        "https://app.test?a=1",
        "https://app.test#x",
    ],
)
def test_registration_rejects_unsafe_base_url(url):
    with pytest.raises(BitrixConfigurationError) as caught:
        BitrixRegistrationService(settings=settings(url))
    assert "password" not in str(caught.value)


def test_robot_payload_nested_and_bracket_are_normalized_without_mutation():
    source = {
        "auth[member_id]": "member",
        "auth[application_token]": "application-secret",
        "EVENT_TOKEN": "event-secret",
        "PROPERTIES[delay_seconds]": "10",
        "PROPERTIES[comment]": " note ",
    }
    original = source.copy()
    value = normalize_robot_payload(source, minimum_delay=1, maximum_delay=20)
    assert value.delay_seconds == 10 and value.comment == "note"
    assert source == original
    assert "secret" not in repr(value)


def test_robot_payload_rejects_bool_decimal_and_long_comment():
    base = {"auth": {"member_id": "m", "application_token": "a"}, "event_token": "e"}
    for delay, comment in ((True, None), ("10.5", None), (10, "x" * 1001)):
        with pytest.raises(RobotPayloadError):
            normalize_robot_payload(
                {**base, "properties": {"delay_seconds": delay, "comment": comment}},
                minimum_delay=1,
                maximum_delay=20,
            )
