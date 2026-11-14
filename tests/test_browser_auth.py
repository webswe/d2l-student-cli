from __future__ import annotations

import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from d2l_cli.browser_auth import BrowserAuth, CookieRecord
from d2l_cli.client.errors import InvalidBaseURLError, SSOLoginFailedError

BASE_URL = "https://example.brightspace.com/"


class FakePage:
    def __init__(self, final_url: str, *, raise_on_wait: Exception | None = None) -> None:
        self.final_url = final_url
        self._raise_on_wait = raise_on_wait
        self.goto_calls: list[tuple[str, str]] = []

    def goto(self, url: str, *, wait_until: str) -> None:
        self.goto_calls.append((url, wait_until))

    def wait_for_url(self, predicate: Callable[[str], bool], *, timeout: float) -> None:
        if self._raise_on_wait is not None:
            raise self._raise_on_wait
        assert predicate(self.final_url)


class FakeContext:
    def __init__(self, page: FakePage, cookies: list[dict[str, Any]], events: list[str]) -> None:
        self._page = page
        self._cookies = cookies
        self._events = events

    def new_page(self) -> FakePage:
        return self._page

    def cookies(self, urls: list[str]) -> list[dict[str, Any]]:
        return self._cookies

    def close(self) -> None:
        self._events.append("context.close")


class FakeBrowser:
    def __init__(self, context: FakeContext, events: list[str]) -> None:
        self._context = context
        self._events = events

    def new_context(self) -> FakeContext:
        return self._context

    def close(self) -> None:
        self._events.append("browser.close")


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self._browser = browser
        self.launch_calls: list[bool] = []

    def launch(self, *, headless: bool) -> FakeBrowser:
        self.launch_calls.append(headless)
        return self._browser


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium


def _make_factory(playwright: FakePlaywright) -> Callable[[], Any]:
    @contextmanager
    def factory() -> Iterator[FakePlaywright]:
        yield playwright

    return factory


def _build(
    *,
    final_url: str = BASE_URL,
    raise_on_wait: Exception | None = None,
    raw_cookies: list[dict[str, Any]] | None = None,
) -> tuple[BrowserAuth, list[str]]:
    events: list[str] = []
    page = FakePage(final_url, raise_on_wait=raise_on_wait)
    context = FakeContext(page, raw_cookies if raw_cookies is not None else [_raw_cookie()], events)
    browser = FakeBrowser(context, events)
    chromium = FakeChromium(browser)
    playwright = FakePlaywright(chromium)
    auth = BrowserAuth(playwright_factory=_make_factory(playwright), headless=True)
    return auth, events


def _raw_cookie(name: str = "d2lSessionVal", value: str = "topsecretvalue") -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "domain": "example.brightspace.com",
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "sameSite": "Lax",
    }


def test_login_happy_path_returns_scoped_cookies() -> None:
    auth, events = _build()
    result = auth.login(BASE_URL)
    assert result.base_url == BASE_URL
    assert len(result.cookies) == 1
    cookie = result.cookies[0]
    assert cookie.name == "d2lSessionVal"
    assert cookie.value.reveal() == "topsecretvalue"
    assert "context.close" in events
    assert "browser.close" in events


def test_login_closes_context_and_browser_even_on_wait_failure() -> None:
    auth, events = _build(raise_on_wait=TimeoutError("timed out"))
    with pytest.raises(SSOLoginFailedError):
        auth.login(BASE_URL)
    assert "context.close" in events
    assert "browser.close" in events


def test_login_raises_sso_login_failed_when_wait_times_out() -> None:
    auth, _ = _build(raise_on_wait=TimeoutError("timed out"))
    with pytest.raises(SSOLoginFailedError):
        auth.login(BASE_URL)


@pytest.mark.parametrize(
    "lookalike_url",
    [
        "https://example.brightspace.com.evil.invalid/",
        "http://example.brightspace.com/",
        "https://example.brightspace.com:444/",
        "https://example.brightspace.com@evil.invalid/",
        "https://fictional-user:fictional-secret@example.brightspace.com/",
    ],
)
def test_login_return_requires_exact_https_origin(lookalike_url: str) -> None:
    auth, events = _build(final_url=lookalike_url)

    with pytest.raises(SSOLoginFailedError):
        auth.login(BASE_URL)

    assert "context.close" in events
    assert "browser.close" in events


def test_login_accepts_same_origin_path_query_and_fragment() -> None:
    auth, _ = _build(final_url=f"{BASE_URL}d2l/home?fictional=1#section")

    assert auth.login(BASE_URL).base_url == BASE_URL


def test_login_rejects_invalid_base_url_before_browser_factory_runs() -> None:
    factory_called = False

    def factory() -> Any:
        nonlocal factory_called
        factory_called = True
        raise AssertionError("browser factory must not run")

    auth = BrowserAuth(playwright_factory=factory)

    with pytest.raises(InvalidBaseURLError):
        auth.login("http://example.brightspace.com/")

    assert factory_called is False


def test_login_failure_traceback_suppresses_underlying_sensitive_message() -> None:
    fictional_secret = "FICTIONAL_LOGIN_TOKEN_123"
    auth, _ = _build(raise_on_wait=RuntimeError(fictional_secret))

    with pytest.raises(SSOLoginFailedError) as exc_info:
        auth.login(BASE_URL)

    rendered = "".join(
        traceback.format_exception(
            exc_info.type,
            exc_info.value,
            exc_info.tb,
            chain=True,
        )
    )
    assert fictional_secret not in rendered


def test_login_raises_when_no_cookies_observed() -> None:
    auth, _ = _build(raw_cookies=[])
    with pytest.raises(SSOLoginFailedError):
        auth.login(BASE_URL)


def test_login_uses_headless_setting_when_launching() -> None:
    events: list[str] = []
    page = FakePage(BASE_URL)
    context = FakeContext(page, [_raw_cookie()], events)
    browser = FakeBrowser(context, events)
    chromium = FakeChromium(browser)
    playwright = FakePlaywright(chromium)
    auth = BrowserAuth(playwright_factory=_make_factory(playwright), headless=True)
    auth.login(BASE_URL)
    assert chromium.launch_calls == [True]


def test_cookie_record_repr_never_reveals_value() -> None:
    from d2l_cli.secret import SecretStr

    record = CookieRecord(
        name="d2lSessionVal",
        value=SecretStr("topsecretvalue"),
        domain="example.brightspace.com",
        path="/",
        secure=True,
        http_only=True,
        same_site="Lax",
    )
    assert "topsecretvalue" not in repr(record)


def test_browser_auth_result_repr_never_reveals_value() -> None:
    auth, _ = _build()
    result = auth.login(BASE_URL)
    assert "topsecretvalue" not in repr(result)
