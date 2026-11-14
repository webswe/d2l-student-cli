from __future__ import annotations

import logging
from collections.abc import Mapping

import httpx
import pytest
import respx

from d2l_cli.client.errors import (
    NotAuthenticatedError,
    UnexpectedProviderError,
    UnsafeRequestTargetError,
)
from d2l_cli.client.http import D2LHttpClient

_BASE = "https://example.brightspace.com"
_INDEX = "/d2l/lms/grades/index.d2l"
_FINAL = "/d2l/lms/grades/my_grades/main.d2l"


@respx.mock
def test_one_redirect_get_validates_then_uses_ordinary_text_boundary() -> None:
    first = respx.get(f"{_BASE}{_INDEX}").mock(
        return_value=httpx.Response(302, headers={"location": f"{_FINAL}?ou=620000"})
    )
    second = respx.get(f"{_BASE}{_FINAL}").mock(
        return_value=httpx.Response(
            200,
            text="<d2l-table-wrapper></d2l-table-wrapper>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    seen_locations: list[str] = []

    def validate(location: str) -> tuple[str, Mapping[str, str]]:
        seen_locations.append(location)
        return _FINAL, {"ou": "620000"}

    with D2LHttpClient(_BASE) as client:
        response = client.get_text_following_one_redirect(
            _INDEX,
            params={"ou": "620000"},
            validate_redirect=validate,
        )

    assert response.status_code == 200
    assert seen_locations == [f"{_FINAL}?ou=620000"]
    assert first.calls.last.request.method == "GET"
    assert second.calls.last.request.method == "GET"
    assert second.calls.last.request.url.params["ou"] == "620000"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="direct response"),
        httpx.Response(302),
    ],
)
@respx.mock
def test_one_redirect_get_requires_an_advertised_redirect(
    response: httpx.Response,
) -> None:
    respx.get(f"{_BASE}{_INDEX}").mock(return_value=response)
    with D2LHttpClient(_BASE) as client:
        with pytest.raises(UnexpectedProviderError):
            client.get_text_following_one_redirect(
                _INDEX,
                params={"ou": "620000"},
                validate_redirect=lambda location: (_FINAL, {"ou": "620000"}),
            )


@respx.mock
def test_one_redirect_get_rejects_a_second_redirect() -> None:
    respx.get(f"{_BASE}{_INDEX}").mock(
        return_value=httpx.Response(302, headers={"location": f"{_FINAL}?ou=620000"})
    )
    respx.get(f"{_BASE}{_FINAL}").mock(
        return_value=httpx.Response(302, headers={"location": "/d2l/other"})
    )
    with D2LHttpClient(_BASE) as client:
        with pytest.raises(UnexpectedProviderError):
            client.get_text_following_one_redirect(
                _INDEX,
                params={"ou": "620000"},
                validate_redirect=lambda location: (_FINAL, {"ou": "620000"}),
            )


@respx.mock
def test_one_redirect_get_preserves_expired_session_detection() -> None:
    respx.get(f"{_BASE}{_INDEX}").mock(
        return_value=httpx.Response(
            302, headers={"location": "/d2l/login?sessionExpired=1"}
        )
    )
    called = False

    def validate(location: str) -> tuple[str, Mapping[str, str]]:
        nonlocal called
        called = True
        return _FINAL, {"ou": "620000"}

    with D2LHttpClient(_BASE) as client:
        with pytest.raises(NotAuthenticatedError):
            client.get_text_following_one_redirect(
                _INDEX,
                params={"ou": "620000"},
                validate_redirect=validate,
            )
    assert called is False


@respx.mock
def test_redirect_validator_cannot_bypass_same_origin_boundary() -> None:
    auth_calls = 0

    def auth() -> dict[str, str]:
        nonlocal auth_calls
        auth_calls += 1
        return {"Cookie": "d2lSessionVal=private"}

    respx.get(f"{_BASE}{_INDEX}").mock(
        return_value=httpx.Response(302, headers={"location": "/observed"})
    )
    with D2LHttpClient(_BASE, auth_header_provider=auth) as client:
        with pytest.raises(UnsafeRequestTargetError):
            client.get_text_following_one_redirect(
                _INDEX,
                params={"ou": "620000"},
                validate_redirect=lambda location: (
                    "https://foreign.invalid/steal",
                    None,
                ),
            )
    assert auth_calls == 1


@respx.mock
def test_http_debug_log_omits_path_and_query_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_path_id = "9988776655"
    private_course_id = "620000"
    path = f"/d2l/private/{private_path_id}/records"
    respx.get(f"{_BASE}{path}").mock(return_value=httpx.Response(200, json={}))

    with caplog.at_level(logging.DEBUG, logger="d2l_cli.http"):
        with D2LHttpClient(_BASE) as client:
            client.get(path, params={"ou": private_course_id})

    assert private_path_id not in caplog.text
    assert private_course_id not in caplog.text
    assert "param_names=('ou',)" in caplog.text
