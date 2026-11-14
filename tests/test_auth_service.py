from __future__ import annotations

from collections.abc import Mapping

import pytest

from d2l_cli.auth_service import AuthService, AuthState
from d2l_cli.browser_auth import BrowserAuthResult, CookieRecord
from d2l_cli.client.errors import (
    NetworkUnavailableError,
    NotAuthenticatedError,
    SecureStorageUnavailableError,
    UnexpectedProviderError,
)
from d2l_cli.secret import SecretStr
from d2l_cli.session_store import SessionStore

BASE_URL = "https://example.brightspace.com/"


class FakeCredentialStore:
    """In-memory CredentialStore, duplicated from test_session_store.py's
    fixture of the same name to keep this test module independently
    importable (tests/ has no package __init__.py)."""

    def __init__(self, *, available: bool = True) -> None:
        self._data: dict[str, str] = {}
        self._available = available

    def check_available(self) -> None:
        if not self._available:
            raise SecureStorageUnavailableError("fake backend unavailable")

    def save(self, key: str, value: str) -> None:
        self.check_available()
        self._data[key] = value

    def load(self, key: str) -> str | None:
        self.check_available()
        return self._data.get(key)

    def delete(self, key: str) -> None:
        self.check_available()
        self._data.pop(key, None)


class FakeBrowserAuth:
    def __init__(self, result: BrowserAuthResult | None = None) -> None:
        self.result = result
        self.login_calls: list[str] = []

    def login(self, base_url: str) -> BrowserAuthResult:
        self.login_calls.append(base_url)
        assert self.result is not None
        return self.result


def _cookie(name: str = "d2lSessionVal", value: str = "secret") -> CookieRecord:
    return CookieRecord(
        name=name,
        value=SecretStr(value),
        domain="example.brightspace.com",
        path="/",
        secure=True,
        http_only=True,
        same_site="Lax",
    )


def _service(
    *,
    credential_store: FakeCredentialStore | None = None,
    browser_result: BrowserAuthResult | None = None,
    verify_session: object = None,
) -> tuple[AuthService, FakeCredentialStore, FakeBrowserAuth]:
    backend = credential_store or FakeCredentialStore()
    session_store = SessionStore(backend)
    browser_auth = FakeBrowserAuth(browser_result)

    def default_verify(headers: Mapping[str, str]) -> None:
        return None

    service = AuthService(
        session_store=session_store,
        browser_auth=browser_auth,
        verify_session=verify_session or default_verify,  # type: ignore[arg-type]
    )
    return service, backend, browser_auth


def test_login_saves_session_material() -> None:
    result = BrowserAuthResult(base_url=BASE_URL, cookies=(_cookie(),))
    service, backend, browser_auth = _service(browser_result=result)
    service.login(BASE_URL)
    assert browser_auth.login_calls == [BASE_URL]
    assert backend.load("session") is not None


def test_login_does_not_open_browser_when_storage_unavailable() -> None:
    backend = FakeCredentialStore(available=False)
    service, _, browser_auth = _service(credential_store=backend)
    with pytest.raises(SecureStorageUnavailableError):
        service.login(BASE_URL)
    assert browser_auth.login_calls == []


def test_header_provider_raises_when_no_session_stored() -> None:
    service, _, _ = _service()
    with pytest.raises(NotAuthenticatedError):
        service.header_provider()


def test_header_provider_returns_cookie_header() -> None:
    result = BrowserAuthResult(base_url=BASE_URL, cookies=(_cookie(value="thevalue"),))
    service, _, _ = _service(browser_result=result)
    service.login(BASE_URL)
    headers = service.header_provider()
    assert headers == {"Cookie": "d2lSessionVal=thevalue"}


def test_status_not_authenticated_when_no_session_stored() -> None:
    service, _, _ = _service()
    result = service.status()
    assert result.state == AuthState.NOT_AUTHENTICATED


def test_status_authenticated_when_verification_succeeds() -> None:
    browser_result = BrowserAuthResult(base_url=BASE_URL, cookies=(_cookie(),))
    service, _, _ = _service(browser_result=browser_result)
    service.login(BASE_URL)
    result = service.status()
    assert result.state == AuthState.AUTHENTICATED


def test_status_session_expired_when_provider_rejects() -> None:
    def verify(headers: Mapping[str, str]) -> None:
        raise NotAuthenticatedError("rejected")

    browser_result = BrowserAuthResult(base_url=BASE_URL, cookies=(_cookie(),))
    service, _, _ = _service(browser_result=browser_result, verify_session=verify)
    service.login(BASE_URL)
    result = service.status()
    assert result.state == AuthState.SESSION_EXPIRED


def test_status_provider_unavailable_on_network_error() -> None:
    def verify(headers: Mapping[str, str]) -> None:
        raise NetworkUnavailableError("dns failure")

    browser_result = BrowserAuthResult(base_url=BASE_URL, cookies=(_cookie(),))
    service, _, _ = _service(browser_result=browser_result, verify_session=verify)
    service.login(BASE_URL)
    result = service.status()
    assert result.state == AuthState.PROVIDER_UNAVAILABLE


def test_status_provider_unavailable_on_unexpected_provider_error() -> None:
    def verify(headers: Mapping[str, str]) -> None:
        raise UnexpectedProviderError("500")

    browser_result = BrowserAuthResult(base_url=BASE_URL, cookies=(_cookie(),))
    service, _, _ = _service(browser_result=browser_result, verify_session=verify)
    service.login(BASE_URL)
    result = service.status()
    assert result.state == AuthState.PROVIDER_UNAVAILABLE


def test_status_storage_unavailable() -> None:
    backend = FakeCredentialStore(available=False)
    service, _, _ = _service(credential_store=backend)
    result = service.status()
    assert result.state == AuthState.STORAGE_UNAVAILABLE


def test_status_stored_session_unreadable() -> None:
    backend = FakeCredentialStore()
    backend.save("session", "not-json")
    service, _, _ = _service(credential_store=backend)
    result = service.status()
    assert result.state == AuthState.STORED_SESSION_UNREADABLE
