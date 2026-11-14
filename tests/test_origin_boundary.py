"""Phase 3C: authenticated requests are bound to the configured Brightspace
origin, enforced centrally in ``D2LHttpClient``.

Contract under test: request targets are RELATIVE Brightspace paths only.
Any absolute URL (even one on the configured origin), scheme-relative target,
or malformed path is rejected locally, BEFORE any request is built, logged or
sent -- so the session Cookie can never be transmitted to an off-origin host
through client target input. Every host/credential below is synthetic; the
"foreign" hosts are respx mocks and no network is ever contacted.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from d2l_cli.client.errors import (
    D2LError,
    InvalidBaseURLError,
    NotAuthenticatedError,
    PermissionDeniedError,
    ProviderEndpointChangedError,
    UnexpectedProviderError,
    UnsafeRequestTargetError,
)
from d2l_cli.client.http import D2LHttpClient

BASE = "https://example.brightspace.com"
COOKIE = "COOKIE_SENTINEL_a91f"
QUERY_SECRET = "QUERY_SECRET_7c2e"
USERINFO_SECRET = "USERINFO_SECRET_5d10"
FRAGMENT_SECRET = "FRAGMENT_SECRET_3b88"

FIXED_MESSAGE = "request target must be a relative Brightspace path"


def _client() -> D2LHttpClient:
    return D2LHttpClient(BASE, auth_header_provider=lambda: {"Cookie": f"sid={COOKIE}"})


# Every absolute / scheme-relative / malformed target, all rejected uniformly.
REJECTED_TARGETS = [
    # foreign hosts
    f"https://evil.example/x?token={QUERY_SECRET}",
    "https://evil.example/",
    # sibling / parent / child subdomains are different origins
    "https://other.brightspace.com/x",
    "https://brightspace.com/x",
    "https://sub.example.brightspace.com/x",
    # scheme and port mismatches
    "http://example.brightspace.com/x",
    "https://example.brightspace.com:8443/x",
    "http://example.brightspace.com:443/x",
    # userinfo and suffix tricks
    f"https://example.brightspace.com@evil.example/x?k={USERINFO_SECRET}",
    f"https://{USERINFO_SECRET}:pw@evil.example/x",
    "https://example.brightspace.com.evil.example/x",
    "https://evil.example/?next=https://example.brightspace.com",
    "https://evil.example/x#" + FRAGMENT_SECRET,
    # scheme-relative
    "//evil.example/x",
    "//example.brightspace.com/x",
    "///evil.example/x",
    # absolute same-origin: rejected too (relative-only contract)
    "https://example.brightspace.com/x",
    "https://example.brightspace.com:443/x",
    "HTTPS://EXAMPLE.BRIGHTSPACE.COM/x",
    "https://Example.Brightspace.Com/x",
    # not a rooted relative path
    "x",
    "evil.example/x",
    "example.brightspace.com/x",
    "",
    "?a=1",
    "#frag",
    "javascript:alert(1)",
    "data:text/plain,hi",
    "file:///etc/passwd",
    # backslash / whitespace / control-character shapes
    "/\\evil.example/x",
    "\\\\evil.example\\x",
    "\\/evil.example/x",
    "/x\\y",
    " /x",
    "/x y",
    "/x\n",
    "/x\r\nHost: evil.example",
    "/x\x00",
    "/x#" + FRAGMENT_SECRET,
]


@pytest.fixture
def foreign() -> Any:
    """Mocks every foreign host a rejected target could name; must stay unused."""
    with respx.mock(assert_all_called=False) as mock:
        mock.route().mock(return_value=httpx.Response(200, text="foreign"))
        yield mock


# --- reproduction of the original defect (kept as the regression anchor) ----


def test_absolute_foreign_target_is_rejected_before_any_network_and_cookie_never_sent(
    foreign: Any,
) -> None:
    """Before Phase 3C, httpx honored an absolute target over the base URL and
    the Cookie header rode along to the foreign host."""
    with _client() as client, pytest.raises(UnsafeRequestTargetError):
        client.get("https://evil.example/steal")
    assert foreign.calls.call_count == 0  # zero requests, hence zero Cookie sent


# --- rejection matrix --------------------------------------------------------


@pytest.mark.parametrize("target", REJECTED_TARGETS)
@pytest.mark.parametrize("method", ["get", "get_text"])
def test_unsafe_target_is_rejected_before_network(foreign: Any, method: str, target: str) -> None:
    with _client() as client:
        with pytest.raises(UnsafeRequestTargetError):
            getattr(client, method)(target)
    assert foreign.calls.call_count == 0
    assert len(respx.calls) == 0


@pytest.mark.parametrize("target", REJECTED_TARGETS)
def test_rejection_message_is_fixed_and_leaks_nothing(target: str) -> None:
    with _client() as client, pytest.raises(UnsafeRequestTargetError) as exc_info:
        client.get(target)
    rendered = f"{exc_info.value!s} | {exc_info.value!r}"
    assert str(exc_info.value) == FIXED_MESSAGE
    for secret in (QUERY_SECRET, USERINFO_SECRET, FRAGMENT_SECRET, COOKIE, "evil", "example"):
        assert secret not in rendered
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize("target", REJECTED_TARGETS)
def test_rejected_target_is_never_logged(target: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with _client() as client, pytest.raises(UnsafeRequestTargetError):
        client.get(target)
    for secret in (QUERY_SECRET, USERINFO_SECRET, FRAGMENT_SECRET, COOKIE, "evil.example"):
        assert secret not in caplog.text


def test_rejection_is_a_distinct_client_side_error_type() -> None:
    assert issubclass(UnsafeRequestTargetError, D2LError)
    for other in (
        NotAuthenticatedError,
        PermissionDeniedError,
        UnexpectedProviderError,
        ProviderEndpointChangedError,
    ):
        assert not issubclass(UnsafeRequestTargetError, other)
        assert not issubclass(other, UnsafeRequestTargetError)


def test_non_string_target_is_rejected() -> None:
    with _client() as client:
        for bad in (None, b"/x", 5):
            with pytest.raises(UnsafeRequestTargetError):
                client.get(bad)  # type: ignore[arg-type]


# --- relative paths keep working; Cookie flows to the configured origin ------


@respx.mock
@pytest.mark.parametrize("method", ["get", "get_text"])
def test_relative_path_succeeds_and_carries_the_cookie_to_the_configured_origin(
    method: str,
) -> None:
    route = respx.get(f"{BASE}/d2l/x").mock(
        return_value=httpx.Response(
            200, json={"ok": 1}, headers={"content-type": "text/html; application/json"}
        )
        if method == "get_text"
        else httpx.Response(200, json={"ok": 1})
    )
    with _client() as client:
        getattr(client, method)("/d2l/x")
    sent = route.calls.last.request
    assert sent.url.host == "example.brightspace.com"
    assert sent.url.scheme == "https"
    assert sent.headers["Cookie"] == f"sid={COOKIE}"


@respx.mock
def test_relative_path_with_query_string_succeeds() -> None:
    route = respx.get(f"{BASE}/d2l/x").mock(return_value=httpx.Response(200, json={}))
    with _client() as client:
        client.get("/d2l/x?a=1&b=2")
    sent = route.calls.last.request
    assert sent.url.host == "example.brightspace.com"
    assert sent.url.params["a"] == "1" and sent.url.params["b"] == "2"


@respx.mock
def test_relative_path_with_params_argument_succeeds() -> None:
    route = respx.get(f"{BASE}/d2l/x").mock(return_value=httpx.Response(200, json={}))
    with _client() as client:
        client.get("/d2l/x", params={"c": "3"})
    sent = route.calls.last.request
    assert sent.url.host == "example.brightspace.com"
    assert sent.url.params["c"] == "3"


@respx.mock
def test_query_values_containing_absolute_urls_cannot_redirect_the_request() -> None:
    route = respx.get(f"{BASE}/d2l/x").mock(return_value=httpx.Response(200, json={}))
    with _client() as client:
        client.get("/d2l/x?next=https://evil.example/x", params={"u": "//evil.example/y"})
    sent = route.calls.last.request
    assert sent.url.host == "example.brightspace.com"
    assert sent.headers["Cookie"] == f"sid={COOKIE}"


@respx.mock
def test_dot_segments_cannot_leave_the_origin() -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200, json={}))
    with _client() as client:
        client.get("/a/../../x")
    assert route.calls.last.request.url.host == "example.brightspace.com"


@respx.mock
def test_percent_encoded_slashes_stay_inside_the_path() -> None:
    route = respx.route(host="example.brightspace.com").mock(
        return_value=httpx.Response(200, json={})
    )
    with _client() as client:
        client.get("/%2F%2Fevil.example/x")
    assert route.calls.last.request.url.host == "example.brightspace.com"


# --- defense in depth: the built request's parsed origin is re-verified ------


def test_built_request_with_a_different_parsed_origin_is_refused_before_send(
    foreign: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    hijacked: Callable[..., httpx.Request] = lambda *a, **k: httpx.Request(  # noqa: E731
        "GET", "https://evil.example/x", headers={"Cookie": f"sid={COOKIE}"}
    )
    monkeypatch.setattr(client._client, "build_request", hijacked)
    with client, pytest.raises(UnsafeRequestTargetError):
        client.get("/x")
    assert foreign.calls.call_count == 0


@pytest.mark.parametrize(
    "hijacked_url",
    [
        "http://example.brightspace.com/x",
        "https://example.brightspace.com:8443/x",
        "https://other.brightspace.com/x",
    ],
)
def test_built_request_scheme_port_or_host_mismatch_is_refused(
    foreign: Any, monkeypatch: pytest.MonkeyPatch, hijacked_url: str
) -> None:
    client = _client()
    monkeypatch.setattr(
        client._client, "build_request", lambda *a, **k: httpx.Request("GET", hijacked_url)
    )
    with client, pytest.raises(UnsafeRequestTargetError):
        client.get("/x")
    assert foreign.calls.call_count == 0


# --- redirects stay locked (Phase 2C) ------------------------------------------


@respx.mock
def test_foreign_redirect_from_the_configured_origin_is_not_followed() -> None:
    respx.get(f"{BASE}/x").mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.example/landing"})
    )
    foreign_route = respx.get("https://evil.example/landing").mock(
        return_value=httpx.Response(200, text="foreign")
    )
    with _client() as client, pytest.raises(UnexpectedProviderError):
        client.get("/x")
    assert foreign_route.call_count == 0


def test_redirects_remain_disabled() -> None:
    with _client() as client:
        assert client._client.follow_redirects is False


# --- configured base URL validation ---------------------------------------------


@pytest.mark.parametrize(
    "base",
    [
        "https://example.brightspace.com",
        "https://example.brightspace.com/",
        "https://example.brightspace.com:443",
        "https://example.brightspace.com:8443/",
        "https://EXAMPLE.Brightspace.com",
    ],
)
def test_valid_base_urls_are_accepted(base: str) -> None:
    D2LHttpClient(base).close()


@pytest.mark.parametrize(
    "base",
    [
        "",
        "example.brightspace.com",
        "//example.brightspace.com",
        "http://example.brightspace.com",  # cleartext would expose the Cookie
        "ftp://example.brightspace.com",
        "file:///tmp",
        "https://",
        "https:///path",
        f"https://{USERINFO_SECRET}:pw@example.brightspace.com",
        f"https://{USERINFO_SECRET}@example.brightspace.com",
        "https://example.brightspace.com/?q=1",
        "https://example.brightspace.com/#frag",
        "https://example.brightspace.com/prefix",
        "https://example.brightspace.com/prefix/",
        "https://exa mple.brightspace.com",
        "https://example.brightspace.com:notaport",
        "https://example.brightspace.com:99999",
    ],
)
def test_invalid_base_urls_are_rejected_without_echoing_them(base: str) -> None:
    with pytest.raises(InvalidBaseURLError) as exc_info:
        D2LHttpClient(base)
    assert USERINFO_SECRET not in f"{exc_info.value!s} {exc_info.value!r}"
    assert base not in str(exc_info.value) or base == ""
    assert issubclass(InvalidBaseURLError, D2LError)


@respx.mock
def test_default_port_base_url_matches_relative_requests() -> None:
    route = respx.get("https://example.brightspace.com/x").mock(
        return_value=httpx.Response(200, json={})
    )
    with D2LHttpClient("https://example.brightspace.com:443") as client:
        client.get("/x")
    assert route.called


@respx.mock
def test_mixed_case_base_host_matches_normalized_hostname() -> None:
    route = respx.get("https://example.brightspace.com/x").mock(
        return_value=httpx.Response(200, json={})
    )
    with D2LHttpClient("https://EXAMPLE.Brightspace.COM") as client:
        client.get("/x")
    assert route.called


@respx.mock
def test_non_default_port_base_url_is_honored_for_relative_requests() -> None:
    route = respx.get("https://example.brightspace.com:8443/x").mock(
        return_value=httpx.Response(200, json={})
    )
    with D2LHttpClient("https://example.brightspace.com:8443") as client:
        client.get("/x")
    assert route.called


# --- surface / TLS / credentials unchanged -----------------------------------------


def test_public_surface_is_still_exactly_get_only_and_close() -> None:
    public = {name for name in dir(D2LHttpClient) if not name.startswith("_")}
    assert public == {
        "get",
        "get_text",
        "get_bytes",
        "get_text_following_one_redirect",
        "close",
    }
    for verb in ("post", "put", "patch", "delete", "head", "request", "send", "stream"):
        assert not hasattr(D2LHttpClient, verb)


def test_tls_verification_still_required() -> None:
    import ssl

    with _client() as client:
        ctx = client._client._transport._pool._ssl_context  # type: ignore[attr-defined]
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True
