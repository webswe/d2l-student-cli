"""Acceptance tests for the REAL pre-save verification chain (offline).

``AuthService.login`` -> the production verifier built by ``cli._build_auth_service`` ->
``D2LHttpClient`` -> ``BrightspaceCourseProvider.list_courses()`` -> status / expired-session
handling and JSON parsing. Only the transport (respx), the browser and the credential store are
fakes. A candidate that the provider does not clearly accept as authenticated must never be saved.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
import respx

import d2l_cli.cli as cli_module
from d2l_cli.auth_service import AuthService
from d2l_cli.client.errors import (
    MalformedResponseError,
    NetworkUnavailableError,
    ProviderEndpointChangedError,
    SSOLoginFailedError,
    UnexpectedProviderError,
)
from d2l_cli.config import Config
from tests.test_auth_login_hardening import (
    BASE_URL,
    FICTIONAL_SECRET,
    OLD_SECRET,
    _Browser,
    _offline_guards,  # noqa: F401  (re-used autouse isolation fixture)
    _preseed_old_session,
    _result,
    _Store,
)

_COURSES_URL = "https://example.brightspace.com/d2l/le/manageCourses/api/mycourses"
_LOGIN_PAGE = (
    "<html><head><title>Sign in</title></head><body>"
    + "<form action='/d2l/lp/auth/login/login.d2l'><input name='userName'></form>"
    + ("<p>padding</p>" * 400)
    + "</body></html>"
)  # larger than the interstitial size bound
_INTERSTITIAL = (
    "<html><head><script>window.location.replace('/d2l/login?target=%2fd2l%2fhome');"
    "</script></head><body></body></html>"
)
_COURSE = {
    "OrgUnitId": 620001,
    "Code": "CS-1301",
    "Name": "Intro",
    "SemesterName": "Fall 2026",
    "IsActive": True,
    "CanAccessCourse": True,
    "IsNewEnrollment": False,
}


def _service(
    monkeypatch: pytest.MonkeyPatch, store: _Store, events: list[str]
) -> tuple[AuthService, _Browser]:
    browser = _Browser(_result(), events)
    monkeypatch.setattr(cli_module, "KeyringCredentialStore", lambda: store)
    monkeypatch.setattr(cli_module, "BrowserAuth", lambda: browser)
    return cli_module._build_auth_service(Config(base_url=BASE_URL)), browser


def _html(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text, headers={"content-type": "text/html; charset=utf-8"})


def _json(body: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        pytest.param(_html(_LOGIN_PAGE), MalformedResponseError, id="200-login-page"),
        pytest.param(_html(_INTERSTITIAL), SSOLoginFailedError, id="200-login-interstitial"),
        pytest.param(
            _html("<html><body><h1>Maintenance</h1></body></html>"),
            MalformedResponseError,
            id="200-unrelated-html",
        ),
        pytest.param(
            httpx.Response(200, text="not json", headers={"content-type": "application/json"}),
            MalformedResponseError,
            id="200-malformed-json",
        ),
        pytest.param(_json([]), ProviderEndpointChangedError, id="200-json-list"),
        pytest.param(_json({}), ProviderEndpointChangedError, id="200-json-no-courses-key"),
        pytest.param(_json({"Courses": "x"}), ProviderEndpointChangedError, id="200-courses-str"),
        pytest.param(_json({"Courses": None}), ProviderEndpointChangedError, id="200-courses-null"),
        pytest.param(
            _json({"Courses": [{"OrgUnitId": 1}]}),
            ProviderEndpointChangedError,
            id="200-course-missing-fields",
        ),
        pytest.param(
            httpx.Response(302, headers={"location": "/d2l/login?sessionExpired=1"}),
            SSOLoginFailedError,
            id="302-to-login",
        ),
        pytest.param(
            httpx.Response(302, headers={"location": "https://login.idp.example/sso"}),
            UnexpectedProviderError,
            id="302-to-identity-provider",
        ),
        pytest.param(
            httpx.Response(302, headers={"location": "/d2l/home"}),
            UnexpectedProviderError,
            id="302-to-other-page",
        ),
        pytest.param(httpx.Response(401), SSOLoginFailedError, id="401"),
        pytest.param(httpx.Response(403), SSOLoginFailedError, id="403"),
        pytest.param(httpx.Response(500), UnexpectedProviderError, id="500"),
        pytest.param(httpx.Response(204), MalformedResponseError, id="204-empty"),
    ],
)
@respx.mock
def test_unauthenticated_or_unexpected_response_is_never_saved(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response, expected: type[Exception]
) -> None:
    route = respx.get(_COURSES_URL).mock(return_value=response)
    store = _Store()
    service, _ = _service(monkeypatch, store, [])
    with pytest.raises(expected):
        service.login(BASE_URL)
    assert route.call_count == 1  # no retry
    assert store.data == {} and store.saves == 0 and store.deletes == 0


@respx.mock
def test_redirect_chain_is_never_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    # A redirect to the IdP, whose target would return a valid-looking body, must stop at hop 1.
    first = respx.get(_COURSES_URL).mock(
        return_value=httpx.Response(302, headers={"location": "https://login.idp.example/sso"})
    )
    idp = respx.get("https://login.idp.example/sso").mock(return_value=_json({"Courses": []}))
    store = _Store()
    service, _ = _service(monkeypatch, store, [])
    with pytest.raises(UnexpectedProviderError):
        service.login(BASE_URL)
    assert first.call_count == 1 and idp.call_count == 0 and store.saves == 0


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("boom"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("bad")],
)
@respx.mock
def test_transport_failures_fail_before_persistence(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    respx.get(_COURSES_URL).mock(side_effect=failure)
    store = _Store()
    service, _ = _service(monkeypatch, store, [])
    with pytest.raises(NetworkUnavailableError):
        service.login(BASE_URL)
    assert store.saves == 0 and store.data == {}


@respx.mock
def test_authenticated_empty_course_list_is_accepted_and_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # {"Courses": []} is a valid authenticated response under the existing list_courses contract
    # (a student with no enrollments). The verifier is NOT weakened or special-cased for it.
    respx.get(_COURSES_URL).mock(return_value=_json({"Courses": [], "Bookmark": None}))
    store = _Store()
    service, _ = _service(monkeypatch, store, [])
    service.login(BASE_URL)
    assert store.saves == 1


@respx.mock
def test_authenticated_course_list_is_accepted_with_only_the_candidate_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = respx.get(_COURSES_URL).mock(return_value=_json({"Courses": [_COURSE]}))
    store = _Store()
    _preseed_old_session(store)
    service, _ = _service(monkeypatch, store, [])
    service.login(BASE_URL)
    sent = route.calls.last.request.headers["cookie"]
    assert sent == f"d2lSessionVal={FICTIONAL_SECRET}"
    assert OLD_SECRET not in sent
    assert route.call_count == 1 and store.saves == 1


@respx.mock
def test_failed_candidate_with_older_valid_session_present_uses_candidate_and_preserves_old(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = respx.get(_COURSES_URL).mock(return_value=_html(_LOGIN_PAGE))
    store = _Store()
    old = _preseed_old_session(store)
    loads_before = store.loads
    service, _ = _service(monkeypatch, store, [])
    with pytest.raises(MalformedResponseError):
        service.login(BASE_URL)
    assert route.calls.last.request.headers["cookie"] == f"d2lSessionVal={FICTIONAL_SECRET}"
    assert store.loads == loads_before
    assert store.data == {"session": old} and store.saves == 0 and store.deletes == 0


@pytest.mark.parametrize(
    "response",
    [_html(_LOGIN_PAGE), _html(_INTERSTITIAL), httpx.Response(401), _json({"Courses": "x"})],
)
@respx.mock
def test_no_candidate_cookie_text_in_errors_or_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, response: httpx.Response
) -> None:
    respx.get(_COURSES_URL).mock(return_value=response)
    store = _Store()
    service, _ = _service(monkeypatch, store, [])
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(Exception) as info:
            service.login(BASE_URL)
    rendered = repr(info.value) + str(info.value) + caplog.text
    assert FICTIONAL_SECRET not in rendered
    assert store.saves == 0
