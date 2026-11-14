"""Detection of an expired-Brightspace-session response.

KSU Brightspace does not signal an expired session with 401/403. Two
shapes were observed live (Phase 2C; see docs/PROVIDER_NOTES.md, "Phase 2C"):

1. **Redirect** -- on a server-rendered page (the Assignment list) the
   provider answers ``3xx`` with a ``Location`` whose path is the Brightspace
   login route (``/d2l/login``), plus a tiny "Object moved" body.
2. **JS interstitial** -- on an API-style read (``mycourses``) the provider
   answers ``200 text/html`` with a tiny page whose only content is a
   ``<script>`` calling ``window.location.replace('<login route>...')`` and
   an empty ``<body>``.

Both are classified here, from structure only, and both map to the same
``NotAuthenticatedError`` in ``D2LHttpClient._check_status`` -- the lowest
layer shared by ``get()`` (JSON) and ``get_text()`` (HTML), so every
vertical gets identical expiry semantics.

False-positive policy: this is deliberately narrow. A ``200 text/html``
response is only an expiry interstitial if ALL of the following hold: it is
small, its ``<body>`` has no element and no text, and a head script
redirects (``window.location.replace``) to the *same-origin* login route.
Login-ish words in ordinary page text, small arbitrary HTML, JSON, and a
normal Assignment page therefore never match. A redirect only counts if its
``Location`` targets the same-origin login route exactly; redirects
anywhere else stay ``UnexpectedProviderError``.

This module never logs, returns, or raises with any part of the response
body, the redirect target, or any header -- callers get a bool, and the
message constant below contains no response-derived text.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

SESSION_EXPIRED_MESSAGE = (
    "Brightspace session has expired or is no longer valid; run 'd2l auth login' to sign in again"
)

_LOGIN_PATH = "/d2l/login"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# The observed interstitial was ~300 bytes; a real page is orders of
# magnitude larger. Anything over this is never treated as an interstitial.
_MAX_INTERSTITIAL_BYTES = 2048
_SCRIPT_REDIRECT_RE = re.compile(
    r"""window\.location\.replace\(\s*(['"])(?P<target>[^'"\r\n]*)\1"""
)


def _is_login_target(target: str, origin_host: str) -> bool:
    """Whether ``target`` (relative, or absolute on the same host) points at
    the login route exactly."""
    try:
        parts = urlsplit(target.strip())
    except ValueError:
        return False
    if parts.scheme or parts.netloc:
        if parts.scheme not in ("http", "https"):
            return False
        if (parts.hostname or "").lower() != origin_host.lower():
            return False
    return parts.path.lower().rstrip("/") == _LOGIN_PATH


class _InterstitialParser(HTMLParser):
    """Collects head script text and whether ``<body>`` has any content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.script_text: list[str] = []
        self.body_has_content = False
        self._in_script = False
        self._in_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            self._in_body = True
        elif self._in_body:
            self.body_has_content = True  # any element (incl. script) inside body
        elif tag == "script":
            self._in_script = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False
        elif tag == "body":
            self._in_body = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self.script_text.append(data)
        elif self._in_body and data.strip():
            self.body_has_content = True


def _is_login_interstitial(response: httpx.Response, origin_host: str) -> bool:
    if "text/html" not in response.headers.get("content-type", "").lower():
        return False
    if len(response.content) > _MAX_INTERSTITIAL_BYTES:
        return False
    parser = _InterstitialParser()
    parser.feed(response.text)
    parser.close()
    if parser.body_has_content:
        return False
    match = _SCRIPT_REDIRECT_RE.search("".join(parser.script_text))
    return match is not None and _is_login_target(match.group("target"), origin_host)


def is_expired_session_response(response: httpx.Response, *, origin_host: str) -> bool:
    """Whether ``response`` is one of the observed expired-session shapes.
    Pure and side-effect free; never exposes any response content."""
    status = response.status_code
    if status in _REDIRECT_STATUSES:
        location = response.headers.get("location")
        return location is not None and _is_login_target(location, origin_host)
    if status == 200:
        return _is_login_interstitial(response, origin_host)
    return False
