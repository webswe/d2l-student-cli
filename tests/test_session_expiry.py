"""Phase 2C: expired-session classification.

The two expiry shapes modeled here were observed live against KSU
Brightspace (see docs/PROVIDER_NOTES.md, "Phase 2C"). Every host/id/token below is
synthetic; the SENTINEL strings exist only to prove nothing from a response
body / redirect target / Cookie header ever reaches an exception message,
log, stdout, or stderr.
"""

from __future__ import annotations

import logging
import ssl
from collections.abc import Mapping

import httpx
import pytest
import respx
from typer.testing import CliRunner

import d2l_cli.cli as cli_module
from d2l_cli.auth_service import AuthService, AuthState
from d2l_cli.browser_auth import BrowserAuth
from d2l_cli.client.errors import (
    MalformedResponseError,
    NotAuthenticatedError,
    PermissionDeniedError,
    ProviderEndpointChangedError,
    SecureStorageUnavailableError,
    UnexpectedProviderError,
)
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.client.session_expiry import SESSION_EXPIRED_MESSAGE, is_expired_session_response
from d2l_cli.providers.brightspace_assignments import BrightspaceAssignmentProvider
from d2l_cli.providers.brightspace_courses import BrightspaceCourseProvider
from d2l_cli.secret import SecretStr
from d2l_cli.session_bridge import SessionMaterial
from d2l_cli.session_store import SessionStore

BASE_URL = "https://example.brightspace.com"
HOST = "example.brightspace.com"
KSU_BASE_URL = "https://kennesaw.view.usg.edu"

TARGET_SENTINEL = "SSO_TARGET_SENTINEL_9f3a"
COOKIE_SENTINEL = "COOKIE_VALUE_SENTINEL_77bd"
BODY_SENTINEL = "BODY_TEXT_SENTINEL_51c0"

# Mirrors the structure observed live for a JSON-API-style read (mycourses):
# tiny 200 text/html, a head <script> doing window.location.replace to the
# login route, empty <body>.
INTERSTITIAL_HTML = (
    "<!DOCTYPE html><html><head><meta charset='utf-8' /><script>"
    "var hash = window.location.hash;if( hash ) hash = '%23' + hash.substring( 1 );"
    f"window.location.replace('/d2l/login?sessionExpired=1&target=%2Fd2l%2F{TARGET_SENTINEL}'"
    " + hash );</script><title></title></head><body></body></html>"
)

# Mirrors the structure observed live for the Assignment list: a 302 to the
# login route with a tiny "Object moved" HTML body.
LOGIN_REDIRECT_LOCATION = f"/d2l/login?target=%2Fd2l%2F{TARGET_SENTINEL}&sessionExpired=1"
REDIRECT_BODY = (
    "<html><head><title>Object moved</title></head><body>\n"
    f'<h2>Object moved to <a href="{LOGIN_REDIRECT_LOCATION}">here</a>.</h2>\n'
    "</body></html>"
)

ASSIGNMENT_LIST_HTML = """
<d2l-table-wrapper>
  <table>
    <tr><th>Assignment</th><th>Status</th><th>Score</th><th>&nbsp;</th></tr>
    <tr>
      <td><a href="/d2l/lms/dropbox/user/folder_submit_files.d2l?db=90210&ou=620000">
        Homework 1</a></td>
      <td>Submitted</td>
      <td>18 / 20</td>
      <td></td>
    </tr>
  </table>
</d2l-table-wrapper>
"""

COURSES_JSON = {
    "Courses": [
        {
            "OrgUnitId": "778899",
            "Code": "CS-1301-01",
            "Name": "Intro to Programming",
            "SemesterName": "Fall 2026",
            "IsActive": True,
            "CanAccessCourse": True,
            "IsNewEnrollment": False,
            "StartDate": None,
            "EndDate": None,
        }
    ],
    "Bookmark": None,
    "Sort": "Name",
}

_HTML = {"content-type": "text/html; charset=UTF-8"}


def _resp(
    status: int, *, text: str = "", headers: Mapping[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, text=text, headers=dict(headers or {}))


def _interstitial() -> httpx.Response:
    return _resp(200, text=INTERSTITIAL_HTML, headers=_HTML)


def _redirect() -> httpx.Response:
    return _resp(302, text=REDIRECT_BODY, headers={**_HTML, "location": LOGIN_REDIRECT_LOCATION})


def _detect(response: httpx.Response) -> bool:
    return is_expired_session_response(response, origin_host=HOST)


# --- detector: positive shapes (tests 1, 2) --------------------------------


def test_representative_js_interstitial_is_detected_despite_http_200() -> None:
    response = _interstitial()
    assert response.status_code == 200
    assert _detect(response) is True


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_to_same_origin_login_route_is_detected(status: int) -> None:
    response = _resp(status, headers={"location": LOGIN_REDIRECT_LOCATION})
    assert _detect(response) is True


def test_redirect_to_absolute_same_host_login_route_is_detected() -> None:
    response = _resp(302, headers={"location": f"https://{HOST}/d2l/login?x=1"})
    assert _detect(response) is True


def test_interstitial_with_double_quoted_target_is_detected() -> None:
    # Quote style alone must not matter -- only the structure does.
    html = (
        '<html><head><script>window.location.replace("/d2l/login?sessionExpired=1");'
        "</script></head><body></body></html>"
    )
    assert _detect(_resp(200, text=html, headers=_HTML)) is True


# --- detector: negatives (tests 3-6) ---------------------------------------


def test_normal_assignment_html_is_not_detected() -> None:
    assert _detect(_resp(200, text=ASSIGNMENT_LIST_HTML, headers=_HTML)) is False


def test_normal_assignment_html_mentioning_login_words_is_not_detected() -> None:
    html = ASSIGNMENT_LIST_HTML + (
        "<p>Please <a href='/d2l/login'>log in</a>; your session expired? "
        "window.location.replace('/d2l/login')</p>"
    )
    assert _detect(_resp(200, text=html, headers=_HTML)) is False


@pytest.mark.parametrize(
    "html",
    [
        "<html><head></head><body></body></html>",  # small, empty, no redirect
        "<html><body>ok</body></html>",  # small arbitrary
        "<html><body><h1>Object moved</h1></body></html>",
        # script redirect, but NOT to the login route
        "<html><head><script>window.location.replace('/d2l/home');</script></head>"
        "<body></body></html>",
        # script redirect to a login-looking path on a FOREIGN host
        "<html><head><script>window.location.replace("
        "'https://evil.example/d2l/login');</script></head><body></body></html>",
        # protocol-relative foreign host
        "<html><head><script>window.location.replace('//evil.example/d2l/login');"
        "</script></head><body></body></html>",
        # javascript: scheme
        "<html><head><script>window.location.replace('javascript:/d2l/login');"
        "</script></head><body></body></html>",
        # login path is only a prefix/suffix of a different route
        "<html><head><script>window.location.replace('/d2l/login/other');</script></head>"
        "<body></body></html>",
        "<html><head><script>window.location.replace('/x/d2l/login');</script></head>"
        "<body></body></html>",
        # right redirect, but the body has visible content -> a real page
        "<html><head><script>window.location.replace('/d2l/login');</script></head>"
        f"<body>{BODY_SENTINEL}</body></html>",
        # right redirect, but the body has an element
        "<html><head><script>window.location.replace('/d2l/login');</script></head>"
        "<body><div></div></body></html>",
        # redirect script placed inside body
        "<html><head></head><body><script>window.location.replace('/d2l/login');"
        "</script></body></html>",
        # login words in plain text only
        "<html><body>Session expired. Please sign in at /d2l/login</body></html>",
        # a different JS mechanism than the one actually observed
        "<html><head><script>window.location.href='/d2l/login';</script></head>"
        "<body></body></html>",
    ],
)
def test_arbitrary_or_lookalike_small_html_is_not_detected(html: str) -> None:
    assert _detect(_resp(200, text=html, headers=_HTML)) is False


def test_oversized_page_with_interstitial_script_is_not_detected() -> None:
    padding = "<!-- " + ("x" * 4000) + " -->"
    html = INTERSTITIAL_HTML.replace("<title>", padding + "<title>")
    assert len(html) > 2048
    assert _detect(_resp(200, text=html, headers=_HTML)) is False


def test_non_html_content_type_is_not_detected() -> None:
    # Same bytes as the interstitial but declared JSON: never sniffed.
    response = _resp(200, text=INTERSTITIAL_HTML, headers={"content-type": "application/json"})
    assert _detect(response) is False


def test_ordinary_json_is_not_detected() -> None:
    response = httpx.Response(200, json=COURSES_JSON)
    assert _detect(response) is False


def test_json_string_containing_login_redirect_text_is_not_detected() -> None:
    response = httpx.Response(200, json={"note": "window.location.replace('/d2l/login')"})
    assert _detect(response) is False


@pytest.mark.parametrize(
    "location",
    [
        "https://login.example/sso",  # foreign host (existing redirect test's shape)
        "https://evil.example/d2l/login",  # foreign host, login-shaped path
        "//evil.example/d2l/login",
        "/d2l/home",
        "/d2l/login/extra",
        "",
    ],
)
def test_other_redirects_are_not_detected(location: str) -> None:
    assert _detect(_resp(302, headers={"location": location})) is False


def test_redirect_without_location_is_not_detected() -> None:
    assert _detect(_resp(302)) is False


@pytest.mark.parametrize("status", [201, 204, 401, 403, 404, 429, 500, 503])
def test_interstitial_body_on_other_status_codes_is_not_detected_by_detector(status: int) -> None:
    # 401/403/429/5xx keep their existing, explicit handling in the client;
    # the detector only speaks for 200 and login redirects.
    assert _detect(_resp(status, text=INTERSTITIAL_HTML, headers=_HTML)) is False


# --- client: central classification for get() and get_text() ---------------


@respx.mock
def test_get_json_path_classifies_interstitial_before_json_parsing() -> None:
    respx.get(f"{BASE_URL}/x").mock(return_value=_interstitial())
    with D2LHttpClient(BASE_URL) as client, pytest.raises(NotAuthenticatedError) as exc_info:
        client.get("/x")
    assert not isinstance(exc_info.value, MalformedResponseError)


@respx.mock
def test_get_text_path_classifies_interstitial_before_content_type_and_parsing() -> None:
    respx.get(f"{BASE_URL}/x").mock(return_value=_interstitial())
    with D2LHttpClient(BASE_URL) as client, pytest.raises(NotAuthenticatedError):
        client.get_text("/x")


@respx.mock
@pytest.mark.parametrize("method", ["get", "get_text"])
def test_login_redirect_is_classified_on_both_paths_and_not_followed(method: str) -> None:
    respx.get(f"{BASE_URL}/x").mock(return_value=_redirect())
    login_route = respx.get(f"{BASE_URL}/d2l/login").mock(return_value=_resp(200, text="LOGIN"))
    with D2LHttpClient(BASE_URL) as client, pytest.raises(NotAuthenticatedError):
        getattr(client, method)("/x")
    assert login_route.called is False  # redirects remain disabled


@respx.mock
def test_same_expired_response_gives_same_error_on_both_paths() -> None:
    respx.get(f"{BASE_URL}/x").mock(return_value=_interstitial())
    errors = []
    with D2LHttpClient(BASE_URL) as client:
        for call in (client.get, client.get_text):
            with pytest.raises(NotAuthenticatedError) as exc_info:
                call("/x")
            errors.append(str(exc_info.value))
    assert errors[0] == errors[1] == SESSION_EXPIRED_MESSAGE


@respx.mock
def test_non_login_redirect_still_raises_unexpected_provider_error() -> None:
    respx.get(f"{BASE_URL}/x").mock(return_value=_resp(302, headers={"location": "/d2l/home"}))
    with D2LHttpClient(BASE_URL) as client, pytest.raises(UnexpectedProviderError):
        client.get("/x")


@respx.mock
def test_401_still_raises_not_authenticated_and_403_permission_denied() -> None:
    respx.get(f"{BASE_URL}/a").mock(return_value=_resp(401))
    respx.get(f"{BASE_URL}/b").mock(return_value=_resp(403))
    with D2LHttpClient(BASE_URL) as client:
        with pytest.raises(NotAuthenticatedError) as auth_exc:
            client.get("/a")
        with pytest.raises(PermissionDeniedError):
            client.get_text("/b")
    assert str(auth_exc.value) == "provider rejected the request as unauthenticated"


# --- errors / redaction (tests 7, 8, 27, 28) --------------------------------


@respx.mock
@pytest.mark.parametrize("response_factory", [_interstitial, _redirect])
@pytest.mark.parametrize("method", ["get", "get_text"])
def test_expiry_error_and_logs_never_expose_body_target_or_cookie(
    response_factory: object, method: str, caplog: pytest.LogCaptureFixture
) -> None:
    respx.get(f"{BASE_URL}/x").mock(return_value=response_factory())  # type: ignore[operator]
    caplog.set_level(logging.DEBUG)
    provider = lambda: {"Cookie": f"d2lSessionVal={COOKIE_SENTINEL}"}  # noqa: E731
    with D2LHttpClient(BASE_URL, auth_header_provider=provider) as client:
        with pytest.raises(NotAuthenticatedError) as exc_info:
            getattr(client, method)("/x")

    rendered = "\n".join([str(exc_info.value), repr(exc_info.value), caplog.text])
    for secret in (
        TARGET_SENTINEL,
        COOKIE_SENTINEL,
        "sessionExpired",
        "window.location",
        "/d2l/login",
    ):
        assert secret not in rendered
    assert str(exc_info.value) == SESSION_EXPIRED_MESSAGE
    assert exc_info.value.__cause__ is None


# --- providers through the REAL client (tests 14-21) ------------------------


def _assignments() -> BrightspaceAssignmentProvider:
    return BrightspaceAssignmentProvider(D2LHttpClient(BASE_URL))


def _courses() -> BrightspaceCourseProvider:
    return BrightspaceCourseProvider(D2LHttpClient(BASE_URL))


_FOLDERS = f"{BASE_URL}/d2l/lms/dropbox/user/folders_list.d2l"
_MYCOURSES = f"{BASE_URL}/d2l/le/manageCourses/api/mycourses"


@respx.mock
def test_normal_assignment_html_still_parses() -> None:
    respx.get(_FOLDERS).mock(return_value=_resp(200, text=ASSIGNMENT_LIST_HTML, headers=_HTML))
    result = _assignments().list_assignments("620000")
    assert [(a.id, a.course_id) for a in result] == [("90210", "620000")]


@respx.mock
@pytest.mark.parametrize("response_factory", [_interstitial, _redirect])
def test_assignment_expiry_is_auth_failure_not_endpoint_drift(response_factory: object) -> None:
    respx.get(_FOLDERS).mock(return_value=response_factory())  # type: ignore[operator]
    with pytest.raises(NotAuthenticatedError) as exc_info:
        _assignments().list_assignments("620000")
    assert not isinstance(exc_info.value, ProviderEndpointChangedError)


@respx.mock
def test_genuinely_changed_assignment_html_is_still_endpoint_changed() -> None:
    # A real, application-shaped page (has body content) without the
    # expected table wrapper: drift, not expiry.
    html = "<html><head><title>Assignments</title></head><body><p>Nothing here</p></body></html>"
    respx.get(_FOLDERS).mock(return_value=_resp(200, text=html, headers=_HTML))
    with pytest.raises(ProviderEndpointChangedError):
        _assignments().list_assignments("620000")


@respx.mock
def test_small_arbitrary_non_auth_html_keeps_existing_endpoint_changed_semantics() -> None:
    respx.get(_FOLDERS).mock(return_value=_resp(200, text="<html></html>", headers=_HTML))
    with pytest.raises(ProviderEndpointChangedError):
        _assignments().list_assignments("620000")


@respx.mock
def test_assignment_json_response_keeps_malformed_semantics() -> None:
    respx.get(_FOLDERS).mock(return_value=httpx.Response(200, json={"error": "nope"}))
    with pytest.raises(MalformedResponseError):
        _assignments().list_assignments("620000")


@respx.mock
def test_normal_course_json_is_unchanged() -> None:
    respx.get(_MYCOURSES).mock(return_value=httpx.Response(200, json=COURSES_JSON))
    page = _courses().list_courses()
    assert [c.id for c in page.courses] == ["778899"]
    assert page.bookmark is None


@respx.mock
@pytest.mark.parametrize("response_factory", [_interstitial, _redirect])
def test_course_expiry_is_auth_failure_not_malformed_json(response_factory: object) -> None:
    respx.get(_MYCOURSES).mock(return_value=response_factory())  # type: ignore[operator]
    with pytest.raises(NotAuthenticatedError) as exc_info:
        _courses().list_courses()
    assert not isinstance(exc_info.value, MalformedResponseError)


@respx.mock
def test_malformed_non_auth_course_response_keeps_existing_semantics() -> None:
    respx.get(_MYCOURSES).mock(return_value=_resp(200, text="<html>not json</html>", headers=_HTML))
    with pytest.raises(MalformedResponseError):
        _courses().list_courses()


# --- auth status via the REAL AuthService/client (tests 9-13) ---------------


class _MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def check_available(self) -> None:
        return None

    def save(self, key: str, value: str) -> None:
        self.data[key] = value

    def load(self, key: str) -> str | None:
        return self.data.get(key)

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


def _seeded_store() -> _MemoryStore:
    store = _MemoryStore()
    SessionStore(store).save(
        SessionMaterial(
            cookie_header=SecretStr(f"d2lSessionVal={COOKIE_SENTINEL}"),
            cookie_names=("d2lSessionVal",),
        )
    )
    return store


def _real_auth_service(store: _MemoryStore, base_url: str) -> AuthService:
    def verify(headers: Mapping[str, str]) -> None:
        client = D2LHttpClient(base_url, auth_header_provider=lambda: headers)
        try:
            BrightspaceCourseProvider(client).list_courses()
        finally:
            client.close()

    return AuthService(
        session_store=SessionStore(store), browser_auth=BrowserAuth(), verify_session=verify
    )


@respx.mock
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_resp(401), AuthState.SESSION_EXPIRED),
        (_resp(403), AuthState.SESSION_EXPIRED),
        (_interstitial(), AuthState.SESSION_EXPIRED),
        (_redirect(), AuthState.SESSION_EXPIRED),
        (_resp(500, text="boom"), AuthState.PROVIDER_UNAVAILABLE),
        (_resp(200, text="<html>not json</html>", headers=_HTML), AuthState.PROVIDER_UNAVAILABLE),
        (_resp(302, headers={"location": "/d2l/home"}), AuthState.PROVIDER_UNAVAILABLE),
        (httpx.Response(200, json=COURSES_JSON), AuthState.AUTHENTICATED),
    ],
)
def test_auth_status_maps_each_response_to_the_right_state(
    response: httpx.Response, expected: AuthState
) -> None:
    respx.get(_MYCOURSES).mock(return_value=response)
    result = _real_auth_service(_seeded_store(), BASE_URL).status()
    assert result.state == expected


@respx.mock
def test_auth_status_network_failure_stays_provider_unavailable() -> None:
    respx.get(_MYCOURSES).mock(side_effect=httpx.ConnectError("refused"))
    result = _real_auth_service(_seeded_store(), BASE_URL).status()
    assert result.state == AuthState.PROVIDER_UNAVAILABLE


@respx.mock
def test_auth_status_expiry_does_not_touch_stored_credentials() -> None:
    respx.get(_MYCOURSES).mock(return_value=_interstitial())
    store = _seeded_store()
    before = dict(store.data)
    _real_auth_service(store, BASE_URL).status()
    assert store.data == before  # not cleared, rewritten, or refreshed


# --- CLI end to end (tests 12, 15, 20, 27, 28) ------------------------------

runner = CliRunner()


class _HeaderAuth:
    def header_provider(self) -> Mapping[str, str]:
        return {"Cookie": f"d2lSessionVal={COOKIE_SENTINEL}"}


def _assert_clean(output: str) -> None:
    for secret in (
        TARGET_SENTINEL,
        COOKIE_SENTINEL,
        "sessionExpired",
        "window.location",
        "<script",
    ):
        assert secret not in output


@respx.mock
def test_cli_auth_status_reports_session_expired_not_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _seeded_store()
    monkeypatch.setattr(cli_module, "KeyringCredentialStore", lambda: store)
    respx.get(f"{KSU_BASE_URL}/d2l/le/manageCourses/api/mycourses").mock(
        return_value=_interstitial()
    )

    result = runner.invoke(cli_module.app, ["auth", "status"])

    assert result.exit_code == 1
    assert result.stdout.startswith("session_expired:")
    assert "provider_unavailable" not in result.stdout
    _assert_clean(result.stdout + result.stderr)
    assert store.data == _seeded_store().data  # credentials untouched


@respx.mock
@pytest.mark.parametrize("response_factory", [_interstitial, _redirect])
def test_cli_courses_list_expiry_message_on_stderr_without_leaks(
    monkeypatch: pytest.MonkeyPatch, response_factory: object
) -> None:
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: _HeaderAuth())
    respx.get(f"{KSU_BASE_URL}/d2l/le/manageCourses/api/mycourses").mock(
        return_value=response_factory()  # type: ignore[operator]
    )

    result = runner.invoke(cli_module.app, ["courses", "list", "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert SESSION_EXPIRED_MESSAGE in result.stderr
    _assert_clean(result.stderr)


@respx.mock
@pytest.mark.parametrize("response_factory", [_interstitial, _redirect])
def test_cli_assignments_list_expiry_message_on_stderr_without_leaks(
    monkeypatch: pytest.MonkeyPatch, response_factory: object
) -> None:
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: _HeaderAuth())
    respx.get(f"{KSU_BASE_URL}/d2l/lms/dropbox/user/folders_list.d2l").mock(
        return_value=response_factory()  # type: ignore[operator]
    )

    result = runner.invoke(cli_module.app, ["assignments", "list", "--course", "620000"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert SESSION_EXPIRED_MESSAGE in result.stderr
    assert "endpoint" not in result.stderr.lower()
    _assert_clean(result.stderr)


@respx.mock
@pytest.mark.parametrize("response_factory", [_interstitial, _redirect])
def test_cli_grades_list_expiry_message_on_stderr_without_leaks(
    monkeypatch: pytest.MonkeyPatch, response_factory: object
) -> None:
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: _HeaderAuth())
    respx.get(f"{KSU_BASE_URL}/d2l/lms/grades/index.d2l?ou=620000").mock(
        return_value=response_factory()  # type: ignore[operator]
    )

    result = runner.invoke(
        cli_module.app,
        ["grades", "list", "--course", "620000", "--json"],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert SESSION_EXPIRED_MESSAGE in result.stderr
    assert "endpoint" not in result.stderr.lower()
    _assert_clean(result.stderr)


# --- security surface unchanged (tests 22-26) --------------------------------


def test_public_client_surface_is_still_get_only() -> None:
    public = {name for name in dir(D2LHttpClient) if not name.startswith("_")}
    assert public == {
        "get",
        "get_text",
        "get_bytes",
        "get_text_following_one_redirect",
        "close",
    }
    for verb in ("post", "put", "patch", "delete", "head", "request"):
        assert not hasattr(D2LHttpClient, verb)


def test_tls_verification_is_still_required_and_redirects_still_disabled() -> None:
    with D2LHttpClient(BASE_URL) as client:
        httpx_client = client._client
        assert httpx_client.follow_redirects is False
        ssl_context = httpx_client._transport._pool._ssl_context  # type: ignore[attr-defined]
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert ssl_context.check_hostname is True


def test_untrusted_credential_backend_is_still_refused() -> None:
    from d2l_cli.credentials import KeyringCredentialStore

    class _Fake:
        class errors:  # noqa: N801
            KeyringError = Exception
            PasswordDeleteError = Exception

        @staticmethod
        def get_keyring() -> object:
            return object()

    with pytest.raises(SecureStorageUnavailableError):
        KeyringCredentialStore(keyring_module=_Fake).check_available()


def test_secretstr_and_session_material_stay_redacted() -> None:
    material = SessionMaterial(
        cookie_header=SecretStr(f"d2lSessionVal={COOKIE_SENTINEL}"), cookie_names=("d2lSessionVal",)
    )
    assert COOKIE_SENTINEL not in repr(material)
    assert COOKIE_SENTINEL not in str(material.cookie_header)
    assert COOKIE_SENTINEL not in repr(material.cookie_header)
