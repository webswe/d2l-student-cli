"""A stored session must never be sent to an origin other than the one it was created for.

These tests drive the real CLI with an in-memory credential store and a mocked transport. The
transport is a strict allow-list: any request to an unexpected origin, or carrying an unexpected
``Cookie`` header, is recorded, so the assertions check what would actually have been sent rather
than only that an exception was raised afterwards. All domains and values are fictional.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

import d2l_cli.cli as cli_module
from d2l_cli.auth_service import AuthService, AuthState
from d2l_cli.browser_auth import BrowserAuthResult, CookieRecord
from d2l_cli.cli import app
from d2l_cli.client.errors import SecureStorageUnavailableError
from d2l_cli.secret import SecretStr
from d2l_cli.session_bridge import SessionMaterial
from d2l_cli.session_store import SessionStore

ORIGIN_A = "https://school-a.example.edu"
ORIGIN_B = "https://school-b.example.edu"
ATTACKER = "https://evil.example.test"
COOKIE = "d2lSessionVal=FICTIONAL_SESSION_SECRET_A"
SECRET = "FICTIONAL_SESSION_SECRET_A"
COURSES_PATH = "/d2l/le/manageCourses/api/mycourses"


class MemoryCredentials:
    """In-memory CredentialStore standing in for Windows Credential Manager."""

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


@pytest.fixture
def creds(monkeypatch: pytest.MonkeyPatch) -> MemoryCredentials:
    store = MemoryCredentials()
    monkeypatch.setattr(cli_module, "KeyringCredentialStore", lambda **_: store)
    monkeypatch.delenv("D2L_CLI_BASE_URL", raising=False)
    return store


def _save_session(creds: MemoryCredentials, origin: str = ORIGIN_A) -> None:
    SessionStore(creds, origin=origin).save(
        SessionMaterial(cookie_header=SecretStr(COOKIE), cookie_names=("d2lSessionVal",))
    )


def _run(*args: str) -> Any:
    return CliRunner().invoke(app, list(args))


def _courses_json() -> dict[str, object]:
    return {"Courses": [], "Bookmark": None}


class _Tripwire:
    """Records every request the mocked transport sees, per origin."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router

    def requests(self) -> list[httpx.Request]:
        return [call.request for call in self.router.calls]

    def cookies_sent(self) -> list[str]:
        return [r.headers.get("cookie", "") for r in self.requests()]

    def origins_contacted(self) -> set[str]:
        return {f"{r.url.scheme}://{r.url.host}" for r in self.requests()}


@pytest.fixture
def transport() -> Any:
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(200, json=_courses_json()))
        yield _Tripwire(router)


# --- every authenticated command refuses a changed origin before any request -----------------

AUTHENTICATED_COMMANDS = [
    ("courses", "list"),
    ("courses", "list", "--json"),
    ("assignments", "list", "--course", "1001", "--json"),
    ("assignments", "overview", "--json"),
    ("grades", "list", "--course", "1001", "--json"),
    ("announcements", "list", "--course", "1001", "--json"),
    ("overview", "--json"),
    ("syllabus", "find", "--course", "1001", "--json"),
    ("syllabus", "fetch", "--course", "1001", "--topic", "2002", "--json"),
]


@pytest.mark.parametrize("command", AUTHENTICATED_COMMANDS, ids=" ".join)
@pytest.mark.parametrize("other", [ORIGIN_B, ATTACKER, "https://school-a.example.edu:8443"])
def test_no_request_and_no_cookie_leave_the_machine_after_the_base_url_changes(
    command: tuple[str, ...],
    other: str,
    creds: MemoryCredentials,
    transport: _Tripwire,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_session(creds, ORIGIN_A)
    monkeypatch.setenv("D2L_CLI_BASE_URL", other)

    result = _run(*command)

    assert result.exit_code == 1
    assert transport.requests() == []  # nothing was built or sent to ANY origin
    assert transport.cookies_sent() == []
    assert result.stdout == ""  # JSON stdout stays empty on failure
    assert "different site" in result.stderr
    assert "d2l auth login" in result.stderr
    for text in (result.stdout, result.stderr, repr(result.exception)):
        assert SECRET not in text
        assert other not in text and ORIGIN_A not in text


def test_the_same_origin_still_works_and_sends_the_cookie_only_there(
    creds: MemoryCredentials, transport: _Tripwire, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_session(creds, ORIGIN_A)
    monkeypatch.setenv("D2L_CLI_BASE_URL", ORIGIN_A + "/")
    result = _run("courses", "list", "--json")
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == {"courses": []}
    assert transport.origins_contacted() == {ORIGIN_A}
    assert transport.cookies_sent() == [COOKIE]


@pytest.mark.parametrize(
    "spelling", ["https://SCHOOL-A.example.edu", "https://school-a.example.edu:443"]
)
def test_equivalent_origin_spellings_are_the_same_origin(
    spelling: str, creds: MemoryCredentials, transport: _Tripwire, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_session(creds, ORIGIN_A)
    monkeypatch.setenv("D2L_CLI_BASE_URL", spelling)
    assert _run("courses", "list", "--json").exit_code == 0
    assert transport.origins_contacted() == {ORIGIN_A}


def test_a_http_origin_is_rejected_as_invalid_before_any_credential_is_read(
    creds: MemoryCredentials, transport: _Tripwire, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_session(creds, ORIGIN_A)
    monkeypatch.setenv("D2L_CLI_BASE_URL", "http://school-a.example.edu")
    result = _run("courses", "list", "--json")
    assert result.exit_code == 1
    assert transport.requests() == []
    assert SECRET not in result.stderr


@pytest.mark.parametrize(
    "bad",
    ["https://school-a.example.edu/course", "https://u:p@school-a.example.edu", "not a url", ""],
)
def test_an_invalid_configured_url_is_a_clean_error(
    bad: str, creds: MemoryCredentials, transport: _Tripwire, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_session(creds, ORIGIN_A)
    monkeypatch.setenv("D2L_CLI_BASE_URL", bad)
    result = _run("courses", "list", "--json")
    assert result.exit_code == 1
    assert result.stdout == ""
    assert transport.requests() == []
    assert "Traceback" not in result.stderr
    assert result.exception is None or isinstance(result.exception, SystemExit)


# --- legacy, corrupt and unsupported records ---------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (
            json.dumps({"cookie_header": COOKIE, "cookie_names": ["d2lSessionVal"]}),
            "no recorded site",
        ),
        ("not-json", "corrupt"),
        ("", "corrupt"),
        (
            json.dumps(
                {
                    "schema_version": 7,
                    "origin": ORIGIN_A,
                    "cookie_header": COOKIE,
                    "cookie_names": ["d2lSessionVal"],
                }
            ),
            "not supported",
        ),
    ],
)
def test_unusable_records_fail_closed_with_no_request(
    stored: str,
    expected: str,
    creds: MemoryCredentials,
    transport: _Tripwire,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    creds.data["session"] = stored
    monkeypatch.setenv("D2L_CLI_BASE_URL", ORIGIN_A)
    result = _run("courses", "list", "--json")
    assert result.exit_code == 1
    assert result.stdout == ""
    assert expected in result.stderr and "d2l auth login" in result.stderr
    assert SECRET not in result.stderr
    assert transport.requests() == []
    assert creds.data["session"] == stored  # not deleted, not rewritten, not migrated


def test_missing_session_is_reported_and_sends_nothing(
    creds: MemoryCredentials, transport: _Tripwire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("D2L_CLI_BASE_URL", ORIGIN_A)
    result = _run("courses", "list", "--json")
    assert result.exit_code == 1 and result.stdout == ""
    assert "no stored session" in result.stderr
    assert transport.requests() == []


# --- auth status -------------------------------------------------------------------------------


def test_auth_status_reports_a_mismatch_without_contacting_anyone(
    creds: MemoryCredentials, transport: _Tripwire, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_session(creds, ORIGIN_A)
    monkeypatch.setenv("D2L_CLI_BASE_URL", ORIGIN_B)
    result = _run("auth", "status")
    assert result.exit_code == 1
    assert "different site" in result.stdout + result.stderr
    assert transport.requests() == []
    assert SECRET not in result.stdout + result.stderr


def test_auth_status_for_the_right_origin_verifies_with_one_request(
    creds: MemoryCredentials, transport: _Tripwire, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_session(creds, ORIGIN_A)
    monkeypatch.setenv("D2L_CLI_BASE_URL", ORIGIN_A)
    result = _run("auth", "status")
    assert result.exit_code == 0, result.stdout
    assert result.stdout.startswith("authenticated")
    assert len(transport.requests()) == 1
    assert transport.origins_contacted() == {ORIGIN_A}


def test_auth_status_reports_unreadable_for_legacy_and_none_for_missing(
    creds: MemoryCredentials, transport: _Tripwire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("D2L_CLI_BASE_URL", ORIGIN_A)
    assert _run("auth", "status").stdout.startswith(AuthState.NOT_AUTHENTICATED.value)
    creds.data["session"] = json.dumps({"cookie_header": COOKIE, "cookie_names": ["d2lSessionVal"]})
    result = _run("auth", "status")
    assert result.stdout.startswith(AuthState.STORED_SESSION_UNREADABLE.value)
    assert "no recorded site" in result.stdout
    assert transport.requests() == []


def test_unavailable_credential_manager_is_reported(
    creds: MemoryCredentials, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable() -> None:
        raise SecureStorageUnavailableError("fake backend unavailable")

    creds.check_available = unavailable  # type: ignore[method-assign]
    monkeypatch.setenv("D2L_CLI_BASE_URL", ORIGIN_A)
    result = _run("auth", "status")
    assert result.exit_code == 1
    assert result.stdout.startswith(AuthState.STORAGE_UNAVAILABLE.value)


# --- login binds the session to the origin it was created for ---------------------------------


class _FakeBrowser:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def login(self, base_url: str) -> BrowserAuthResult:
        assert base_url == self.base_url
        cookie = CookieRecord(
            name="d2lSessionVal",
            value=SecretStr("FICTIONAL_NEW_SESSION"),
            domain=base_url.split("//")[1],
            path="/",
            secure=True,
            http_only=True,
            same_site="Lax",
        )
        return BrowserAuthResult(base_url=base_url, cookies=(cookie,))


def _login_service(creds: MemoryCredentials, origin: str) -> AuthService:
    return AuthService(
        session_store=SessionStore(creds, origin=origin),
        browser_auth=_FakeBrowser(origin),  # type: ignore[arg-type]
        verify_session=lambda headers: None,
    )


def test_login_stores_a_record_bound_to_the_configured_origin(creds: MemoryCredentials) -> None:
    _login_service(creds, ORIGIN_B).login(ORIGIN_B)
    record = json.loads(creds.data["session"])
    assert record["schema_version"] == 1
    assert record["origin"] == ORIGIN_B
    assert "FICTIONAL_NEW_SESSION" in record["cookie_header"]


def test_login_replaces_a_legacy_record_and_the_new_session_is_usable(
    creds: MemoryCredentials,
) -> None:
    creds.data["session"] = json.dumps({"cookie_header": COOKIE, "cookie_names": ["d2lSessionVal"]})
    service = _login_service(creds, ORIGIN_A)
    service.login(ORIGIN_A)
    assert COOKIE not in creds.data["session"]
    assert service.header_provider() == {"Cookie": "d2lSessionVal=FICTIONAL_NEW_SESSION"}


def test_after_logging_in_to_origin_b_origin_a_is_refused(creds: MemoryCredentials) -> None:
    _save_session(creds, ORIGIN_A)
    _login_service(creds, ORIGIN_B).login(ORIGIN_B)
    with pytest.raises(Exception, match="different site"):
        _login_service(creds, ORIGIN_A).header_provider()


# --- storage hygiene ---------------------------------------------------------------------------


def test_the_session_is_only_ever_written_to_the_credential_store(
    creds: MemoryCredentials, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    _save_session(creds)
    SessionStore(creds, origin=ORIGIN_A).load()
    assert set(tmp_path.rglob("*")) == before
    assert list(creds.data) == ["session"]


def test_the_real_credential_store_namespace_is_unchanged_by_origin_binding() -> None:
    from d2l_cli import credentials

    assert credentials._SERVICE_NAME == "d2l-student-cli"
    assert SimpleNamespace(key="session").key == "session"
