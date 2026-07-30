"""Robot definition, registry, registration, and callback payload tests."""

import pytest

from app.bitrix.client import BitrixResponse
from app.bitrix.exceptions import BitrixConfigurationError
from app.bitrix.registration import (
    DUPLICATE_ACTIVITY_CODES,
    BitrixRegistrationService,
    _robot_codes,
)
from app.config import Settings
from app.robots.payload import (
    RobotPayloadError,
    normalize_rest_request_payload,
    normalize_robot_payload,
    normalize_wait_field_payload,
)
from app.robots.registry import activity_registry, robot_registry
from app.robots.rest_request import REST_REQUEST_ACTIVITY_CODE, REST_REQUEST_ROBOT
from app.robots.short_pause import SHORT_PAUSE_CODE, SHORT_PAUSE_ROBOT
from app.robots.wait_field import WAIT_FIELD_ACTIVITY_CODE, WAIT_FIELD_ROBOT


class FakeClient:
    def __init__(self, robots=None, events=None, activities=None):
        self.robots = robots or []
        self.events = events or []
        self.activities = activities or []
        self.calls = []

    async def call(self, method, params=None):
        self.calls.append((method, params))
        if method == "bizproc.robot.list":
            result = self.robots
        elif method == "bizproc.activity.list":
            result = self.activities
        elif method == "bizproc.activity.delete":
            code = params["CODE"]
            self.activities = [item for item in self.activities if item.get("CODE") != code]
            result = True
        elif method == "event.get":
            result = self.events
        else:
            result = True
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


def _subscription_source(properties):
    return {
        "auth": {"member_id": "member", "application_token": "application-secret"},
        "event_token": "event-secret",
        "properties": properties,
    }


def test_registries_contain_only_the_three_crm_robots():
    definitions = robot_registry.all()
    assert activity_registry.all() == ()
    assert definitions == (SHORT_PAUSE_ROBOT, REST_REQUEST_ROBOT, WAIT_FIELD_ROBOT)
    assert all(
        item.fields("https://app.test/callback")["USE_SUBSCRIPTION"] == "Y" for item in definitions
    )


async def test_registration_deletes_only_known_duplicate_activities():
    unknown_code = "another.application.activity"
    client = FakeClient(
        activities=[
            {"CODE": REST_REQUEST_ACTIVITY_CODE},
            {"CODE": WAIT_FIELD_ACTIVITY_CODE},
            {"CODE": unknown_code},
        ]
    )

    result = await BitrixRegistrationService(settings=settings()).ensure_activities_registered(
        client
    )
    repeated = await BitrixRegistrationService(settings=settings()).ensure_activities_registered(
        client
    )

    assert result == {code: "deleted" for code in sorted(DUPLICATE_ACTIVITY_CODES)}
    assert repeated == {}
    assert [
        params["CODE"] for method, params in client.calls if method.endswith(".delete")
    ] == sorted(DUPLICATE_ACTIVITY_CODES)
    assert client.activities == [{"CODE": unknown_code}]


async def test_activity_cleanup_is_idempotent_and_does_not_delete_short_pause():
    client = FakeClient(activities=[{"CODE": SHORT_PAUSE_CODE}])
    service = BitrixRegistrationService(settings=settings())

    first = await service.ensure_activities_registered(client)
    second = await service.ensure_activities_registered(client)

    assert first == second == {}
    assert [method for method, _ in client.calls].count("bizproc.activity.list") == 2
    assert not any(method == "bizproc.activity.delete" for method, _ in client.calls)


def test_rest_request_payload_validates_json_method_auth_and_jsonpath():
    value = normalize_rest_request_payload(
        _subscription_source(
            {
                "rest_method": "lists.element.get",
                "request_params_json": '{"FILTER":{"ID":1}}',
                "jsonpath": "$[*].ID",
                "error_recipients": ["user_7", 8],
            }
        )
    )
    assert value.properties["request_params"] == {"FILTER": {"ID": 1}}
    assert value.error_recipients == (7, 8)
    invalid = (
        {"rest_method": "../method", "request_params_json": "{}", "jsonpath": "$"},
        {"rest_method": "method", "request_params_json": "[]", "jsonpath": "$"},
        {"rest_method": "method", "request_params_json": '{"auth":"x"}', "jsonpath": "$"},
        {"rest_method": "method", "request_params_json": "{", "jsonpath": "$"},
        {"rest_method": "method", "request_params_json": "{}", "jsonpath": "$["},
    )
    for properties in invalid:
        with pytest.raises(RobotPayloadError):
            normalize_rest_request_payload(_subscription_source(properties))


def test_wait_field_payload_normalizes_positive_integers():
    value = normalize_wait_field_payload(
        _subscription_source(
            {
                "entity_type_id": "2",
                "entity_id": 10,
                "field_name": "UF_CRM_VALUE",
                "poll_interval_seconds": "30",
                "timeout_seconds": 300,
            }
        )
    )
    assert value.properties["entity_type_id"] == 2
    assert value.properties["poll_interval_seconds"] == 30


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


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ([SHORT_PAUSE_CODE, "another.robot"], {SHORT_PAUSE_CODE, "another.robot"}),
        (
            {"activities": [{"CODE": SHORT_PAUSE_CODE}, {"code": "another.robot"}]},
            {SHORT_PAUSE_CODE, "another.robot"},
        ),
        ({"result": {"robots": [SHORT_PAUSE_CODE]}}, {SHORT_PAUSE_CODE}),
    ],
)
def test_robot_codes_supports_bitrix_response_shapes(payload, expected):
    assert _robot_codes(payload) == expected


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
