"""Mock-only tests for the universal Bitrix24 REST client."""

import json
import logging
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
import respx

from app.bitrix.client import (
    BitrixClient,
    normalize_client_endpoint,
    parse_retry_after,
    validate_rest_method,
)
from app.bitrix.exceptions import (
    BitrixAuthenticationError,
    BitrixConfigurationError,
    BitrixInvalidResponseError,
    BitrixPermanentError,
    BitrixPermissionError,
    BitrixRateLimitError,
    BitrixTemporaryError,
    BitrixTimeoutError,
    BitrixTransportError,
)

ENDPOINT = "https://portal.test/rest/"
METHOD_URL = f"{ENDPOINT}bizproc.robot.list"
ACCESS_TOKEN = "test-access-token"


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("https://portal.test/rest", ENDPOINT),
        (ENDPOINT, ENDPOINT),
        ("https://portal.test/custom/rest///", "https://portal.test/custom/rest/"),
    ],
)
def test_endpoint_validation_and_normalization(endpoint: str, expected: str) -> None:
    assert normalize_client_endpoint(endpoint) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://portal.test/rest/",
        "https:///rest/",
        "https://test-user@portal.test/rest/",
        "https://test-user:test-password@portal.test/rest/",
        "https://portal.test/rest/?access_token=test-value",
        "https://portal.test/rest/#fragment",
        "https://portal.test/rest/ with-space",
    ],
)
def test_invalid_endpoint_is_rejected_without_echo(endpoint: str) -> None:
    with pytest.raises(BitrixConfigurationError) as captured:
        normalize_client_endpoint(endpoint)
    assert "test-password" not in str(captured.value)
    assert endpoint not in str(captured.value)


@pytest.mark.parametrize("method", ["bizproc.robot.list", "bizproc.event.send"])
def test_valid_rest_method(method: str) -> None:
    assert validate_rest_method(method) == method


@pytest.mark.parametrize(
    "method",
    [
        "",
        "bizproc/robot/list",
        "https://portal.test/rest/method",
        "..",
        "method?x",
        "method#x",
        "bad method",
    ],
)
def test_invalid_rest_method(method: str) -> None:
    with pytest.raises(BitrixConfigurationError):
        validate_rest_method(method)


@pytest.mark.parametrize("result", [True, [1, 2], {"id": 1}, None, "value", 5])
@respx.mock
async def test_successful_result_types_and_request_contract(result) -> None:
    route = respx.post(METHOD_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": result,
                "next": 50,
                "total": 120,
                "time": {"duration": 0.01, "operating_reset_at": 12345},
            },
        )
    )
    params = {"filter": {"ACTIVE": "Y"}}
    original = {"filter": {"ACTIVE": "Y"}}

    async with BitrixClient(client_endpoint=ENDPOINT, access_token=ACCESS_TOKEN) as client:
        response = await client.call("bizproc.robot.list", params=params)

    request = route.calls.last.request
    body = json.loads(request.content)
    assert request.method == "POST"
    assert request.headers["Content-Type"].startswith("application/json")
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == "sinedis-bitrix24-automation/0.1.0"
    assert body["auth"] == ACCESS_TOKEN
    assert body["filter"] == params["filter"]
    assert params == original
    assert response.result == result
    assert response.next == 50
    assert response.total == 120
    assert response.time == {"duration": 0.01, "operating_reset_at": 12345}


async def test_params_cannot_override_auth() -> None:
    client = BitrixClient(client_endpoint=ENDPOINT, access_token=ACCESS_TOKEN)
    try:
        with pytest.raises(BitrixConfigurationError):
            await client.call("bizproc.robot.list", {"auth": "different-test-token"})
    finally:
        await client.aclose()


@respx.mock
async def test_external_http_client_is_not_closed() -> None:
    respx.post(METHOD_URL).mock(return_value=httpx.Response(200, json={"result": True}))
    external = httpx.AsyncClient()
    async with BitrixClient(
        client_endpoint=ENDPOINT, access_token=ACCESS_TOKEN, http_client=external
    ) as client:
        await client.call("bizproc.robot.list")
    assert external.is_closed is False
    await external.aclose()


async def test_owned_http_client_is_closed_by_context_manager() -> None:
    client = BitrixClient(client_endpoint=ENDPOINT, access_token=ACCESS_TOKEN)
    owned = client._http_client
    async with client:
        assert owned.is_closed is False
    assert owned.is_closed is True


@pytest.mark.parametrize(
    ("code", "status", "expected_type", "retryable"),
    [
        ("expired_token", 401, BitrixAuthenticationError, False),
        ("NO_AUTH_FOUND", 401, BitrixAuthenticationError, False),
        ("ACCESS_DENIED", 403, BitrixPermissionError, False),
        ("INVALID_CREDENTIALS", 403, BitrixPermissionError, False),
        ("user_access_error", 403, BitrixPermissionError, False),
        ("QUERY_LIMIT_EXCEEDED", 429, BitrixRateLimitError, True),
        ("OPERATION_TIME_LIMIT", 429, BitrixRateLimitError, True),
        ("OVERLOAD_LIMIT", 503, BitrixPermanentError, False),
        ("INTERNAL_SERVER_ERROR", 500, BitrixTemporaryError, True),
        ("ERROR_UNEXPECTED_ANSWER", 502, BitrixTemporaryError, True),
        ("UNKNOWN_BAD_REQUEST", 400, BitrixPermanentError, False),
        ("UNKNOWN_SERVICE_FAILURE", 503, BitrixTemporaryError, True),
    ],
)
@respx.mock
async def test_api_error_classification(code, status, expected_type, retryable) -> None:
    raw_description = f"private body access_token={ACCESS_TOKEN}"
    respx.post(METHOD_URL).mock(
        return_value=httpx.Response(
            status,
            json={
                "error": code,
                "error_description": raw_description,
                "time": {"operating_reset_at": 12345},
            },
            headers={"Retry-After": "7"},
        )
    )

    async with BitrixClient(client_endpoint=ENDPOINT, access_token=ACCESS_TOKEN) as client:
        with pytest.raises(expected_type) as captured:
            await client.call("bizproc.robot.list")

    error = captured.value
    assert error.code == code
    assert error.http_status == status
    assert error.retryable is retryable
    assert ACCESS_TOKEN not in str(error)
    assert raw_description not in str(error)
    assert error.time == {"operating_reset_at": 12345}
    if isinstance(error, BitrixRateLimitError):
        assert error.retry_after_seconds == 7


@pytest.mark.parametrize(
    ("response", "expected_type"),
    [
        (
            httpx.Response(
                200, text="<html>test body</html>", headers={"Content-Type": "text/html"}
            ),
            BitrixInvalidResponseError,
        ),
        (
            httpx.Response(200, content=b"not-json", headers={"Content-Type": "application/json"}),
            BitrixInvalidResponseError,
        ),
        (httpx.Response(200, json={"time": {}}), BitrixInvalidResponseError),
        (httpx.Response(500, text="server body"), BitrixTemporaryError),
        (httpx.Response(502, text="gateway body"), BitrixTemporaryError),
        (httpx.Response(400, json={"result": True}), BitrixPermanentError),
        (httpx.Response(200, json=[{"result": True}]), BitrixInvalidResponseError),
    ],
)
@respx.mock
async def test_invalid_and_unstructured_responses(response, expected_type) -> None:
    respx.post(METHOD_URL).mock(return_value=response)
    async with BitrixClient(client_endpoint=ENDPOINT, access_token=ACCESS_TOKEN) as client:
        with pytest.raises(expected_type) as captured:
            await client.call("bizproc.robot.list")
    assert "server body" not in str(captured.value)
    assert "gateway body" not in str(captured.value)


@respx.mock
async def test_json_error_is_classified_even_with_http_200() -> None:
    respx.post(METHOD_URL).mock(
        return_value=httpx.Response(
            200, json={"error": "ACCESS_DENIED", "error_description": "private"}
        )
    )
    async with BitrixClient(client_endpoint=ENDPOINT, access_token=ACCESS_TOKEN) as client:
        with pytest.raises(BitrixPermissionError):
            await client.call("bizproc.robot.list")


@pytest.mark.parametrize(
    ("transport_error", "expected_type"),
    [
        (httpx.ConnectTimeout("timeout access_token=test-access-token"), BitrixTimeoutError),
        (httpx.ReadTimeout("timeout access_token=test-access-token"), BitrixTimeoutError),
        (httpx.ConnectError("connect access_token=test-access-token"), BitrixTransportError),
        (httpx.RequestError("request access_token=test-access-token"), BitrixTransportError),
    ],
)
@respx.mock
async def test_transport_error_conversion(transport_error, expected_type) -> None:
    route = respx.post(METHOD_URL).mock(side_effect=transport_error)
    async with BitrixClient(client_endpoint=ENDPOINT, access_token=ACCESS_TOKEN) as client:
        with pytest.raises(expected_type) as captured:
            await client.call("bizproc.robot.list")
    assert captured.value.__cause__ is transport_error
    assert ACCESS_TOKEN not in str(captured.value)
    assert route.call_count == 1


def test_retry_after_seconds_http_date_and_invalid_value() -> None:
    now = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    assert parse_retry_after("12", now=now) == 12
    assert parse_retry_after(format_datetime(now + timedelta(seconds=9)), now=now) == 9
    assert parse_retry_after("invalid", now=now) is None
    assert parse_retry_after(None, now=now) is None


@respx.mock
async def test_logging_contains_only_safe_call_metadata(caplog) -> None:
    route = respx.post(METHOD_URL)
    route.side_effect = [
        httpx.Response(200, json={"result": True, "private": "full-success-body"}),
        httpx.Response(
            400,
            json={
                "error": "ACCESS_DENIED",
                "error_description": "full-error-body access_token=test-access-token",
            },
        ),
    ]
    caplog.set_level(logging.DEBUG, logger="app.bitrix.client")
    async with BitrixClient(client_endpoint=ENDPOINT, access_token=ACCESS_TOKEN) as client:
        await client.call("bizproc.robot.list", {"password": "test-request-password"})
        with pytest.raises(BitrixPermissionError):
            await client.call("bizproc.robot.list")

    output = caplog.text
    assert "bizproc.robot.list" in output
    assert ACCESS_TOKEN not in output
    assert "test-request-password" not in output
    assert "full-success-body" not in output
    assert "full-error-body" not in output
    assert "Authorization" not in output
