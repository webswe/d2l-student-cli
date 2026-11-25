"""Offline regression tests for login hardening.

Covers (1) browser completion no longer being proven by the origin alone, and (2) the captured
candidate session being verified, in memory, before anything is persisted.

Everything external is a fake: no browser, no socket, no real keyring. Autouse guards below make
any accidental real access fail the test. Only fictional secrets are used.
"""

from __future__ import annotations

import socket
import subprocess
import traceback
import webbrowser
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import keyring
import pytest
from typer.testing import CliRunner

import d2l_cli.browser_auth as browser_auth_module
import d2l_cli.cli as cli_module
from d2l_cli.auth_service import AuthService, AuthState
from d2l_cli.browser_auth import BrowserAuth, BrowserAuthResult, CookieRecord
from d2l_cli.cli import app
from d2l_cli.client.errors import (
    AuthExpiredError,
    CredentialPortalMismatchError,
    CredentialReadError,
    D2LError,
    MalformedResponseError,
    NetworkUnavailableError,
    NotAuthenticatedError,
    PermissionDeniedError,
    SecureStorageUnavailableError,
    SSOLoginFailedError,
    TLSVerificationError,
    UnexpectedProviderError,
)
from d2l_cli.credentials import KeyringCredentialStore
from d2l_cli.secret import SecretStr
from d2l_cli.session_store import SessionStore

BASE_URL = "https://example.brightspace.com/"
OTHER_URL = "https://other.brightspace.example/"
FICTIONAL_SECRET = "FICTIONAL_COOKIE_SECRET_9f3a"
OLD_SECRET = "FICTIONAL_OLD_SESSION_77b2"
WINCRED_BLOB_LIMIT = 2560

runner = CliRunner()


# --------------------------------------------------------------------------- isolation guards
class _ForbiddenAccess(AssertionError):
    pass


def _forbid(what: str) -> Callable[..., Any]:
    def refuse(*_a: object, **_k: object) -> Any:
        raise _ForbiddenAccess(f"real {what} access attempted in an offline test")

    return refuse


@pytest.fixture(autouse=True)
def _offline_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _forbid("socket"))
    monkeypatch.setattr(socket, "create_connection", _forbid("socket"))
    monkeypatch.setattr(socket, "getaddrinfo", _forbid("DNS"))
    monkeypatch.setattr(subprocess, "Popen", _forbid("subprocess"))
    monkeypatch.setattr(webbrowser, "open", _forbid("browser"))
    for name in ("get_keyring", "get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, _forbid("keyring"))
    monkeypatch.setattr(browser_auth_module, "_default_playwright_factory", _forbid("Playwright"))
    monkeypatch.setenv("D2L_CLI_BASE_URL", BASE_URL)


def test_offline_guards_block_network_keyring_and_browser() -> None:
    with pytest.raises(_ForbiddenAccess):
        socket.create_connection(("example.invalid", 443))
    with pytest.raises(_ForbiddenAccess):
        keyring.get_password("d2l-student-cli", "session")
    with pytest.raises(_ForbiddenAccess):
        browser_auth_module._default_playwright_factory()


# --------------------------------------------------------------------------- browser fakes
class _Page:
    """Replays a navigation history; ``wait_for_url`` succeeds on the first URL the predicate
    accepts and otherwise behaves like a Playwright timeout."""

    def __init__(self, urls: list[str], raise_on_wait: BaseException | None = None) -> None:
        self.urls = urls
        self.raise_on_wait = raise_on_wait

    def goto(self, url: str, *, wait_until: str) -> None:
        return None

    def wait_for_url(self, predicate: Callable[[str], bool], *, timeout: float) -> None:
        if self.raise_on_wait is not None:
            raise self.raise_on_wait
        if not any(predicate(u) for u in self.urls):
            raise TimeoutError("timed out")


class _Context:
    def __init__(self, page: _Page, cookies: list[dict[str, Any]], events: list[str]) -> None:
        self.page, self.raw, self.events = page, cookies, events
        self.cookie_reads = 0

    def new_page(self) -> _Page:
        return self.page

    def cookies(self, urls: list[str]) -> list[dict[str, Any]]:
        self.cookie_reads += 1
        return self.raw

    def close(self) -> None:
        self.events.append("context.close")


def _raw_cookie(value: str = FICTIONAL_SECRET) -> dict[str, Any]:
    return {
        "name": "d2lSessionVal",
        "value": value,
        "domain": "example.brightspace.com",
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "sameSite": "Lax",
    }


def _browser(
    urls: list[str],
    *,
    cookies: list[dict[str, Any]] | None = None,
    raise_on_wait: BaseException | None = None,
) -> tuple[BrowserAuth, _Context, list[str]]:
    events: list[str] = []
    ctx = _Context(
        _Page(urls, raise_on_wait), [_raw_cookie()] if cookies is None else cookies, events
    )
    browser = SimpleNamespace(new_context=lambda: ctx, close=lambda: events.append("browser.close"))
    pw = SimpleNamespace(chromium=SimpleNamespace(launch=lambda *, headless: browser))

    @contextmanager
    def factory() -> Iterator[Any]:
        yield pw

    return BrowserAuth(playwright_factory=factory, headless=True), ctx, events


# --------------------------------------------------------------------------- browser completion
def test_authenticated_landing_page_completes() -> None:
    auth, ctx, events = _browser([f"{BASE_URL}d2l/home"])
    result = auth.login(BASE_URL)
    assert [c.name for c in result.cookies] == ["d2lSessionVal"]
    assert ctx.cookie_reads == 1
    assert events == ["context.close", "browser.close"]


@pytest.mark.parametrize(
    "login_url",
    [
        f"{BASE_URL}d2l/login",
        f"{BASE_URL}d2l/login?sessionExpired=1",
        f"{BASE_URL}d2l/login/",
        f"{BASE_URL}D2L/Login#fragment",
    ],
)
def test_same_origin_login_page_is_not_a_completed_login(login_url: str) -> None:
    # Cookies exist (a login page can set them) and the origin matches, yet it is not success.
    auth, ctx, events = _browser([login_url])
    with pytest.raises(SSOLoginFailedError):
        auth.login(BASE_URL)
    assert ctx.cookie_reads == 0  # cookies are never even read
    assert events == ["context.close", "browser.close"]


@pytest.mark.parametrize(
    "idp_url",
    [
        "https://login.idp.example/saml/sso?SAMLRequest=fictional",
        "https://example.brightspace.com.evil.invalid/d2l/home",
        "http://example.brightspace.com/d2l/home",
        "https://example.brightspace.com:8443/d2l/home",
    ],
)
def test_identity_provider_or_lookalike_origin_is_not_completion(idp_url: str) -> None:
    auth, ctx, _ = _browser([idp_url])
    with pytest.raises(SSOLoginFailedError):
        auth.login(BASE_URL)
    assert ctx.cookie_reads == 0


def test_navigation_sequence_completes_only_at_the_authenticated_page() -> None:
    urls = [
        f"{BASE_URL}d2l/login",
        "https://login.idp.example/sso",
        "https://login.idp.example/mfa",
        f"{BASE_URL}d2l/home",
    ]
    auth, ctx, _ = _browser(urls)
    auth.login(BASE_URL)
    assert ctx.cookie_reads == 1


def test_login_page_then_idp_only_times_out_without_reading_cookies() -> None:
    auth, ctx, _ = _browser([f"{BASE_URL}d2l/login", "https://login.idp.example/sso"])
    with pytest.raises(SSOLoginFailedError):
        auth.login(BASE_URL)
    assert ctx.cookie_reads == 0


def test_empty_cookies_fail_closed() -> None:
    auth, _, _ = _browser([f"{BASE_URL}d2l/home"], cookies=[])
    with pytest.raises(SSOLoginFailedError):
        auth.login(BASE_URL)


def test_timeout_is_translated_and_resources_closed() -> None:
    auth, _, events = _browser([], raise_on_wait=TimeoutError("t"))
    with pytest.raises(SSOLoginFailedError):
        auth.login(BASE_URL)
    assert events == ["context.close", "browser.close"]


def test_user_cancellation_propagates_and_closes_resources() -> None:
    auth, _, events = _browser([], raise_on_wait=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        auth.login(BASE_URL)
    assert events == ["context.close", "browser.close"]


def test_unexpected_wait_error_is_sanitized() -> None:
    auth, _, _ = _browser([], raise_on_wait=RuntimeError(FICTIONAL_SECRET))
    with pytest.raises(SSOLoginFailedError) as info:
        auth.login(BASE_URL)
    rendered = "".join(traceback.format_exception(info.type, info.value, info.tb, chain=True))
    assert FICTIONAL_SECRET not in rendered


def test_browser_start_failure_propagates_and_nothing_is_read() -> None:
    @contextmanager
    def factory() -> Iterator[Any]:
        raise RuntimeError("fictional browser failure")
        yield  # pragma: no cover

    with pytest.raises(RuntimeError):
        BrowserAuth(playwright_factory=factory).login(BASE_URL)


# --------------------------------------------------------------------------- service fakes
class _Store:
    """In-memory CredentialStore that records every operation."""

    def __init__(self, *, available: bool = True, blob_limit: int | None = None) -> None:
        self.data: dict[str, str] = {}
        self.available = available
        self.blob_limit = blob_limit
        self.saves = 0
        self.loads = 0
        self.deletes = 0
        self.fail_save: Exception | None = None

    def check_available(self) -> None:
        if not self.available:
            raise SecureStorageUnavailableError("fake backend unavailable")

    def save(self, key: str, value: str) -> None:
        self.check_available()
        if self.fail_save is not None:
            raise self.fail_save
        if self.blob_limit is not None and len(value.encode()) > self.blob_limit:
            raise SecureStorageUnavailableError("failed to write to secure credential storage")
        self.saves += 1
        self.data[key] = value

    def load(self, key: str) -> str | None:
        self.loads += 1
        return self.data.get(key)

    def delete(self, key: str) -> None:
        self.deletes += 1
        self.data.pop(key, None)


def _result(value: str = FICTIONAL_SECRET, base_url: str = BASE_URL) -> BrowserAuthResult:
    return BrowserAuthResult(
        base_url=base_url,
        cookies=(
            CookieRecord(
                name="d2lSessionVal",
                value=SecretStr(value),
                domain="example.brightspace.com",
                path="/",
                secure=True,
                http_only=True,
                same_site="Lax",
            ),
        ),
    )


class _Browser:
    def __init__(self, result: BrowserAuthResult | BaseException, events: list[str]) -> None:
        self.result, self.events, self.calls = result, events, 0

    def login(self, base_url: str) -> BrowserAuthResult:
        self.calls += 1
        self.events.append("browser")
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _service(
    *,
    store: _Store | None = None,
    browser_result: BrowserAuthResult | BaseException | None = None,
    verify: Callable[[Mapping[str, str]], None] | None = None,
) -> tuple[AuthService, _Store, _Browser, list[str], list[Mapping[str, str]]]:
    store = store or _Store()
    events: list[str] = []
    seen: list[Mapping[str, str]] = []

    def verifier(headers: Mapping[str, str]) -> None:
        events.append("verify")
        seen.append(dict(headers))
        if verify is not None:
            verify(headers)

    browser = _Browser(browser_result if browser_result is not None else _result(), events)
    service = AuthService(
        session_store=SessionStore(store, origin=BASE_URL),
        browser_auth=browser,  # type: ignore[arg-type]
        verify_session=verifier,
    )
    return service, store, browser, events, seen


def _material(value: str) -> Any:
    from d2l_cli.session_bridge import build_session_material

    return build_session_material(_result(value))


def _preseed_old_session(store: _Store) -> str:
    SessionStore(store, origin=BASE_URL).save(_material(OLD_SECRET))
    store.saves = 0  # count only operations made by the login under test
    return store.data["session"]


def _raiser(exc: BaseException) -> Callable[[Mapping[str, str]], None]:
    def verify(headers: Mapping[str, str]) -> None:
        raise exc

    return verify


# --------------------------------------------------------------------------- pre-save verification
def test_verified_candidate_is_saved_exactly_once_in_order() -> None:
    service, store, _, events, seen = _service()
    service.login(BASE_URL)
    assert events == ["browser", "verify"]
    assert seen == [{"Cookie": f"d2lSessionVal={FICTIONAL_SECRET}"}]
    assert store.saves == 1 and store.deletes == 0
    assert FICTIONAL_SECRET in store.data["session"]


@pytest.mark.parametrize(
    "failure",
    [
        NotAuthenticatedError("redirect to login"),
        NotAuthenticatedError("login interstitial"),
        PermissionDeniedError("403"),
        AuthExpiredError("expired"),
    ],
)
def test_rejected_candidate_is_not_saved(failure: D2LError) -> None:
    service, store, _, _, _ = _service(verify=_raiser(failure))
    with pytest.raises(SSOLoginFailedError) as info:
        service.login(BASE_URL)
    assert "nothing was saved" in str(info.value)
    assert store.data == {} and store.saves == 0 and store.deletes == 0


@pytest.mark.parametrize(
    "failure",
    [
        NetworkUnavailableError("simulated network failure"),
        TLSVerificationError("simulated tls"),
        UnexpectedProviderError("500"),
        MalformedResponseError("not json"),
    ],
)
def test_provider_or_network_failure_keeps_its_classification_and_saves_nothing(
    failure: D2LError,
) -> None:
    service, store, _, _, _ = _service(verify=_raiser(failure))
    with pytest.raises(type(failure)):
        service.login(BASE_URL)
    assert store.data == {} and store.saves == 0


@pytest.mark.parametrize("failure", [TimeoutError("verification timed out"), RuntimeError("x")])
def test_unexpected_or_timeout_verification_error_saves_nothing(failure: Exception) -> None:
    service, store, _, _, _ = _service(verify=_raiser(failure))
    with pytest.raises(SSOLoginFailedError) as info:
        service.login(BASE_URL)
    assert "nothing was saved" in str(info.value)
    assert store.saves == 0


def test_ambiguous_same_origin_state_that_the_provider_rejects_is_not_saved() -> None:
    # Models a callback page on the same origin that passes the URL gate but is not authenticated.
    service, store, _, _, _ = _service(verify=_raiser(NotAuthenticatedError("not logged in")))
    with pytest.raises(SSOLoginFailedError):
        service.login(BASE_URL)
    assert store.saves == 0


def test_verifier_receives_the_candidate_and_never_the_old_stored_session() -> None:
    store = _Store()
    old = _preseed_old_session(store)
    loads_before = store.loads
    service, _, _, _, seen = _service(store=store)
    service.login(BASE_URL)
    assert seen == [{"Cookie": f"d2lSessionVal={FICTIONAL_SECRET}"}]
    assert OLD_SECRET not in str(seen)
    assert store.loads == loads_before  # login never reads the stored session
    assert store.data["session"] != old  # replaced only after verification


def test_candidate_is_not_written_to_storage_before_verification() -> None:
    store = _Store()

    def verify(headers: Mapping[str, str]) -> None:
        assert store.saves == 0 and store.data == {}

    service, _, _, _, _ = _service(store=store, verify=verify)
    service.login(BASE_URL)
    assert store.saves == 1


def test_malformed_candidate_is_rejected_before_verification() -> None:
    bad = _result("bad\nvalue")  # control character is not a valid cookie header
    service, store, _, events, _ = _service(browser_result=bad)
    with pytest.raises(CredentialReadError):
        service.login(BASE_URL)
    assert "verify" not in events and store.saves == 0


def test_oversized_candidate_is_rejected_before_verification() -> None:
    service, store, _, events, _ = _service(browser_result=_result("x" * 70_000))
    with pytest.raises(CredentialReadError):
        service.login(BASE_URL)
    assert "verify" not in events and store.saves == 0


def test_login_for_a_different_site_is_refused_before_the_browser_starts() -> None:
    service, store, browser, events, _ = _service()
    with pytest.raises(CredentialPortalMismatchError):
        service.login(OTHER_URL)
    assert browser.calls == 0 and events == [] and store.saves == 0


def test_browser_result_for_a_different_site_is_refused_before_verification() -> None:
    service, store, _, events, _ = _service(browser_result=_result(base_url=OTHER_URL))
    with pytest.raises(CredentialPortalMismatchError):
        service.login(BASE_URL)
    assert "verify" not in events and store.saves == 0


# --------------------------------------------------------------------------- credential store
def test_no_existing_record_and_failed_login_leaves_store_empty() -> None:
    service, store, _, _, _ = _service(verify=_raiser(NotAuthenticatedError("x")))
    with pytest.raises(SSOLoginFailedError):
        service.login(BASE_URL)
    assert store.data == {}


@pytest.mark.parametrize(
    "scenario",
    ["verification-rejected", "browser-failed", "browser-cancelled", "network-down"],
)
def test_existing_valid_record_is_preserved_on_failed_replacement(scenario: str) -> None:
    store = _Store()
    old = _preseed_old_session(store)
    kwargs: dict[str, Any] = {}
    expected: type[BaseException] = SSOLoginFailedError
    if scenario == "verification-rejected":
        kwargs["verify"] = _raiser(NotAuthenticatedError("x"))
    elif scenario == "browser-failed":
        kwargs["browser_result"] = SSOLoginFailedError("timed out")
    elif scenario == "browser-cancelled":
        kwargs["browser_result"] = KeyboardInterrupt()
        expected = KeyboardInterrupt
    else:
        kwargs["verify"] = _raiser(NetworkUnavailableError("down"))
        expected = NetworkUnavailableError
    service, _, _, _, _ = _service(store=store, **kwargs)
    with pytest.raises(expected):
        service.login(BASE_URL)
    assert store.data == {"session": old}
    assert store.saves == 0 and store.deletes == 0


def test_backend_unavailable_stops_before_the_browser_starts() -> None:
    service, _, browser, events, _ = _service(store=_Store(available=False))
    with pytest.raises(SecureStorageUnavailableError):
        service.login(BASE_URL)
    assert browser.calls == 0 and events == []


def test_save_failure_after_verification_preserves_the_old_record() -> None:
    store = _Store()
    old = _preseed_old_session(store)
    store.fail_save = SecureStorageUnavailableError("failed to write to secure credential storage")
    service, _, _, events, _ = _service(store=store)
    with pytest.raises(SecureStorageUnavailableError):
        service.login(BASE_URL)
    assert events == ["browser", "verify"]
    assert store.data == {"session": old} and store.deletes == 0


def test_credential_blob_size_limit_failure_preserves_old_record_without_fallback() -> None:
    store = _Store(blob_limit=WINCRED_BLOB_LIMIT)
    old = _preseed_old_session(store)
    # valid for d2l-cli itself, but larger than Windows Credential Manager accepts
    big = _result("A" * (WINCRED_BLOB_LIMIT + 100))
    service, _, _, _, _ = _service(store=store, browser_result=big)
    with pytest.raises(SecureStorageUnavailableError):
        service.login(BASE_URL)
    assert store.data == {"session": old} and store.saves == 0 and store.deletes == 0


# --------------------------------------------------------------------------- namespace
class _WinVault:
    pass


_WinVault.__module__ = "keyring.backends.Windows"
_WinVault.__qualname__ = "WinVaultKeyring"


class _KeyringErrors:
    class KeyringError(Exception):
        pass

    class PasswordDeleteError(KeyringError):
        pass


def _recording_keyring() -> tuple[Any, list[tuple[str, str, str]], dict[tuple[str, str], str]]:
    calls: list[tuple[str, str, str]] = []
    data: dict[tuple[str, str], str] = {}

    def set_password(service: str, key: str, value: str) -> None:
        calls.append(("set", service, key))
        data[(service, key)] = value

    def get_password(service: str, key: str) -> str | None:
        calls.append(("get", service, key))
        return data.get((service, key))

    def delete_password(service: str, key: str) -> None:
        calls.append(("delete", service, key))

    fake = SimpleNamespace(
        get_keyring=lambda: _WinVault(),
        set_password=set_password,
        get_password=get_password,
        delete_password=delete_password,
        errors=_KeyringErrors,
    )
    return fake, calls, data


def test_login_persistence_and_restoration_use_the_unchanged_namespace() -> None:
    fake, calls, data = _recording_keyring()
    session_store = SessionStore(KeyringCredentialStore(keyring_module=fake), origin=BASE_URL)
    service = AuthService(
        session_store=session_store,
        browser_auth=_Browser(_result(), []),  # type: ignore[arg-type]
        verify_session=lambda headers: None,
    )
    service.login(BASE_URL)
    assert list(data) == [("d2l-student-cli", "session")]
    assert service.header_provider() == {"Cookie": f"d2lSessionVal={FICTIONAL_SECRET}"}
    assert {(s, k) for op, s, k in calls if op in ("set", "get")} == {
        ("d2l-student-cli", "session")
    }
    assert not [c for c in calls if c[0] == "delete"]


def test_rejected_login_makes_no_keyring_write_or_delete() -> None:
    fake, calls, data = _recording_keyring()
    session_store = SessionStore(KeyringCredentialStore(keyring_module=fake), origin=BASE_URL)
    service = AuthService(
        session_store=session_store,
        browser_auth=_Browser(_result(), []),  # type: ignore[arg-type]
        verify_session=_raiser(NotAuthenticatedError("x")),
    )
    with pytest.raises(SSOLoginFailedError):
        service.login(BASE_URL)
    assert data == {} and not [c for c in calls if c[0] in ("set", "delete")]


def test_status_behaviour_is_unchanged() -> None:
    service, _, _, _, _ = _service()
    assert service.status().state == AuthState.NOT_AUTHENTICATED
    service.login(BASE_URL)
    assert service.status().state == AuthState.AUTHENTICATED


# --------------------------------------------------------------------------- secret redaction
def test_hostile_verifier_message_never_surfaces() -> None:
    service, _, _, _, _ = _service(verify=_raiser(RuntimeError(FICTIONAL_SECRET)))
    with pytest.raises(SSOLoginFailedError) as info:
        service.login(BASE_URL)
    rendered = "".join(traceback.format_exception(info.type, info.value, info.tb, chain=True))
    assert FICTIONAL_SECRET not in rendered and FICTIONAL_SECRET not in str(info.value)


def test_cli_failure_output_contains_no_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    service, store, _, _, _ = _service(verify=_raiser(NotAuthenticatedError("x")))
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: service)
    with caplog.at_level("DEBUG"):
        result = runner.invoke(app, ["auth", "login"])
    assert result.exit_code == 1
    assert "login did not complete" in result.stderr
    assert "nothing was saved" in result.stderr
    assert "stored securely" not in result.stdout
    for text in (result.stdout, result.stderr, caplog.text, repr(result.exception)):
        assert FICTIONAL_SECRET not in text
    assert store.saves == 0


def test_cli_success_output_contains_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    service, store, _, _, _ = _service()
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: service)
    result = runner.invoke(app, ["auth", "login"])
    assert result.exit_code == 0
    assert "Login successful; session stored securely." in result.stdout
    assert FICTIONAL_SECRET not in result.stdout + result.stderr
    assert store.saves == 1
