"""Browser-mediated authentication.

Launches an isolated Playwright Chromium browser, lets the human complete
their institution's real SSO + MFA flow manually (see docs/THREAT_MODEL.md
-- this code never sees a password or MFA code, never automates MFA, never
bypasses SSO), and returns only the minimum browser state needed to bridge
into the GET-only ``D2LHttpClient``: cookies scoped to the target
Brightspace origin. See ``d2l_cli.session_bridge`` for what happens to that
state next.

Login-success detection: Phase 0.5 reconnaissance observed that a
successful SSO login flow ends with the browser redirected back to the
Brightspace origin itself (e.g. ``kennesaw.view.usg.edu``) after the
identity-provider hop (see docs/PROVIDER_NOTES.md, "Hosts contacted after
authentication"). This module waits for exactly that -- the page's current
URL returning to the configured Brightspace origin -- using normal browser
navigation state. It does not probe any endpoint (no whoami, no API call)
to determine authentication; see docs/THREAT_MODEL.md, "Stop conditions".

All Playwright-specific calls are isolated to ``BrowserAuth.login()``,
behind small Protocols, so the control flow here is unit-testable with
fakes (see tests/test_browser_auth.py) without a real browser -- the same
separation-of-concerns pattern already used in scripts/recon_lib.py.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from d2l_cli.client.errors import SSOLoginFailedError
from d2l_cli.client.http import _origin_of, _parse_base_url
from d2l_cli.secret import SecretStr

DEFAULT_LOGIN_TIMEOUT_SECONDS = 600.0


@dataclass(frozen=True)
class CookieRecord:
    """One browser cookie, scoped to the Brightspace origin. The value is
    never exposed via repr()/str() -- see ``d2l_cli.secret.SecretStr``."""

    name: str
    value: SecretStr
    domain: str
    path: str
    secure: bool
    http_only: bool
    same_site: str | None

    def __repr__(self) -> str:
        return (
            f"CookieRecord(name={self.name!r}, domain={self.domain!r}, "
            f"path={self.path!r}, secure={self.secure!r}, value=<REDACTED>)"
        )


@dataclass(frozen=True)
class BrowserAuthResult:
    """Result of a completed browser login. Carries only cookies already
    filtered (by Playwright itself, via ``context.cookies(urls=[...])``) to
    the target origin -- this structurally excludes third-party widget
    cookies (e.g. the YuJa-issued cookies observed during Phase 0.5 recon)
    without needing to guess or allowlist individual cookie names."""

    base_url: str
    cookies: tuple[CookieRecord, ...]

    def __repr__(self) -> str:
        names = [c.name for c in self.cookies]
        return f"BrowserAuthResult(base_url={self.base_url!r}, cookie_names={names!r})"


class _PageLike(Protocol):
    def goto(self, url: str, *, wait_until: str) -> object: ...
    def wait_for_url(self, url: Callable[[str], bool], *, timeout: float) -> None: ...




class _ContextLike(Protocol):
    def new_page(self) -> _PageLike: ...
    def cookies(self, urls: list[str]) -> list[dict[str, Any]]: ...
    def close(self) -> None: ...


class _BrowserLike(Protocol):
    def new_context(self, **kwargs: Any) -> _ContextLike: ...
    def close(self) -> None: ...


class _ChromiumLike(Protocol):
    def launch(self, *, headless: bool) -> _BrowserLike: ...


class _PlaywrightLike(Protocol):
    chromium: _ChromiumLike


PlaywrightFactory = Callable[[], AbstractContextManager[_PlaywrightLike]]


def _default_playwright_factory() -> AbstractContextManager[_PlaywrightLike]:
    from typing import cast

    from playwright.sync_api import sync_playwright

    # Real Playwright's context manager yields a `Playwright` object with a
    # much larger surface (firefox, webkit, selectors, ...) than
    # `_PlaywrightLike` declares. It structurally provides everything
    # `_PlaywrightLike` needs (`.chromium.launch(...)`); the cast exists
    # only because Protocol attribute matching is invariant and mypy can't
    # see that compatibility through nested attribute types.
    return cast("AbstractContextManager[_PlaywrightLike]", sync_playwright())


def _matches_origin(url: str, expected_origin: tuple[str, str, int]) -> bool:
    try:
        candidate = httpx.URL(url)
    except (httpx.InvalidURL, ValueError):
        return False
    return not candidate.userinfo and _origin_of(candidate) == expected_origin


def _cookie_record_from_raw(raw: dict[str, Any]) -> CookieRecord:
    return CookieRecord(
        name=raw["name"],
        value=SecretStr(raw["value"]),
        domain=raw.get("domain", ""),
        path=raw.get("path", "/"),
        secure=bool(raw.get("secure", False)),
        http_only=bool(raw.get("httpOnly", False)),
        same_site=raw.get("sameSite"),
    )


def _print_login_instructions(base_url: str) -> None:
    print(
        "Browser window opened. Complete your institution's normal SSO "
        f"login and MFA for {base_url} manually -- this CLI never sees "
        "your password or MFA code, and does not automate this step. "
        "Waiting for the browser to return to the Brightspace site...",
        flush=True,
    )


class BrowserAuth:
    """Drives the manual-SSO browser login flow. All Playwright-specific
    calls live here; the rest of the application only ever sees
    ``BrowserAuthResult``."""

    def __init__(
        self,
        *,
        playwright_factory: PlaywrightFactory = _default_playwright_factory,
        headless: bool = False,
        login_timeout_seconds: float = DEFAULT_LOGIN_TIMEOUT_SECONDS,
    ) -> None:
        self._playwright_factory = playwright_factory
        self._headless = headless
        self._login_timeout_seconds = login_timeout_seconds

    def login(self, base_url: str) -> BrowserAuthResult:
        # Apply the same https-only, plain-origin validation used by the
        # authenticated HTTP boundary before a browser process is started.
        origin = _origin_of(_parse_base_url(base_url))

        with self._playwright_factory() as p:
            browser = p.chromium.launch(headless=self._headless)
            try:
                context = browser.new_context()
                try:
                    return self._run_login(context, base_url, origin)
                finally:
                    context.close()
            finally:
                browser.close()

    def _run_login(
        self,
        context: _ContextLike,
        base_url: str,
        origin: tuple[str, str, int],
    ) -> BrowserAuthResult:
        page = context.new_page()
        page.goto(base_url, wait_until="commit")
        _print_login_instructions(base_url)

        try:
            page.wait_for_url(
                lambda url: _matches_origin(url, origin),
                timeout=self._login_timeout_seconds * 1000,
            )
        except Exception as exc:
            # Deliberately broad: any failure of the underlying wait (e.g.
            # Playwright's own TimeoutError) is translated into this
            # project's own error taxonomy rather than leaking a
            # Playwright-specific exception type to callers.
            raise SSOLoginFailedError(
                f"timed out or failed waiting for SSO/MFA to complete: {exc.__class__.__name__}"
            ) from None

        raw_cookies = context.cookies(urls=[base_url])
        cookies = tuple(_cookie_record_from_raw(c) for c in raw_cookies)
        if not cookies:
            raise SSOLoginFailedError(
                "no session cookies were observed for the configured "
                "Brightspace origin after login completed"
            )
        return BrowserAuthResult(base_url=base_url, cookies=cookies)


