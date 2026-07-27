"""Tests for lifecycle payload normalization and validation."""

from copy import deepcopy

import pytest

from app.bitrix.payload import BitrixPayloadError, normalize_event_payload


def install_auth() -> dict[str, object]:
    return {
        "member_id": "test-member-id",
        "domain": "portal.test",
        "client_endpoint": "https://portal.test/rest/",
        "server_endpoint": "https://oauth.test/rest/",
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "application_token": "test-application-token",
        "expires_in": "3600",
    }


def test_nested_json_install_payload_is_normalized_without_mutation() -> None:
    source = {"event": "ONAPPINSTALL", "ts": 123, "data": {"VERSION": "1"}, "auth": install_auth()}
    original = deepcopy(source)
    payload = normalize_event_payload(
        source, expected_event="ONAPPINSTALL", require_oauth_tokens=True
    )
    assert source == original
    assert payload.auth.member_id == "test-member-id"
    assert payload.auth.expires_in == 3600
    assert payload.data == {"VERSION": "1"}
    rendered = repr(payload)
    assert "test-access-token" not in rendered
    assert "test-refresh-token" not in rendered
    assert "test-application-token" not in rendered


def test_form_bracket_notation_and_multipart_compatible_mapping() -> None:
    auth = install_auth()
    source = {"event": "ONAPPINSTALL", "data[LANGUAGE_ID]": "ru"}
    source.update({f"auth[{key}]": value for key, value in auth.items()})
    payload = normalize_event_payload(
        source, expected_event="ONAPPINSTALL", require_oauth_tokens=True
    )
    assert payload.auth.domain == "portal.test"
    assert payload.data["LANGUAGE_ID"] == "ru"


def test_uninstall_allows_missing_oauth_pair_for_clean_variants() -> None:
    base = install_auth()
    base.pop("access_token")
    base.pop("refresh_token")
    base.pop("expires_in")
    for clean in ("0", "1"):
        payload = normalize_event_payload(
            {"event": "ONAPPUNINSTALL", "auth": base, "data[CLEAN]": clean},
            expected_event="ONAPPUNINSTALL",
            require_oauth_tokens=False,
        )
        assert payload.auth.refresh_token is None
        assert payload.data["CLEAN"] == clean


@pytest.mark.parametrize(
    "mutate",
    [
        lambda auth: auth.pop("member_id"),
        lambda auth: auth.pop("refresh_token"),
        lambda auth: auth.update(expires_in="invalid"),
        lambda auth: auth.update(expires="-1", expires_in=""),
        lambda auth: auth.update(domain="https://portal.test/path"),
    ],
)
def test_invalid_install_payloads_are_rejected(mutate) -> None:
    auth = install_auth()
    mutate(auth)
    with pytest.raises(BitrixPayloadError):
        normalize_event_payload(
            {"event": "ONAPPINSTALL", "auth": auth},
            expected_event="ONAPPINSTALL",
            require_oauth_tokens=True,
        )


def test_wrong_event_is_rejected() -> None:
    with pytest.raises(BitrixPayloadError):
        normalize_event_payload(
            {"event": "ONAPPUNINSTALL", "auth": install_auth()},
            expected_event="ONAPPINSTALL",
            require_oauth_tokens=True,
        )
