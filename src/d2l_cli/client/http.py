"""GET-only HTTP client boundary.

This is the *only* HTTP surface application/command code is allowed to use.
It intentionally does not expose ``post``/``put``/``patch``/``delete``, or a
generic ``request(method, ...)`` — see docs/THREAT_MODEL.md and the Phase 0.5
spec, section "GET-Only Structural Boundary". This is defense in depth: it
does not by itself prove the underlying credential is read-only.

TLS verification is never configurable here — it is always on.

Origin boundary (Phase 3C): every request target must be a RELATIVE
Brightspace path (``/d2l/...``, optionally with a query string). Anything
that could address another origin -- an absolute URL (even one on the
configured origin), a scheme-relative ``//host`` target, a backslash/control
character shape, a target that is not rooted at ``/`` -- is rejected with
``UnsafeRequestTargetError`` BEFORE the auth header provider is consulted,
before any request is built or logged, and before any network I/O, so the
session Cookie can never be sent off-origin through client target input.
As defense in depth, the parsed origin (scheme, host, effective port) of the
request httpx actually built is compared with the configured origin before
it is sent. Production providers only ever pass constant relative paths, so
no absolute-URL support is needed or kept. Redirects are never followed.
"""

from __future__ import annotations

import logging
import re
import ssl
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from d2l_cli.client.errors import (
    InvalidBaseURLError,
    MalformedResponseError,
    NetworkUnavailableError,
    NotAuthenticatedError,
    PermissionDeniedError,
    RateLimitedError,
    TLSVerificationError,
    UnexpectedProviderError,
    UnsafeRequestTargetError,
)
from d2l_cli.client.redact import redact_headers
from d2l_cli.client.session_expiry import SESSION_EXPIRED_MESSAGE, is_expired_session_response

logger = logging.getLogger("d2l_cli.http")

# Auth providers return the headers (e.g. {"Authorization": "Bearer ..."} or
# {"Cookie": "..."}) to attach to each request. Kept as a callable, not a
# stored value, so a token can be refreshed/re-derived per call without this
# client needing to know how.
AuthHeaderProvider = Callable[[], Mapping[str, str]]
RedirectTargetValidator = Callable[[str], tuple[str, Mapping[str, str] | None]]

# Fixed messages: neither the rejected target nor the configured base URL is
# ever echoed (either could carry query data or userinfo).
_TARGET_REJECTED_MESSAGE = "request target must be a relative Brightspace path"
_BASE_URL_REJECTED_MESSAGE = "configured Brightspace base URL must be a plain https origin"
_RESPONSE_TOO_LARGE_MESSAGE = "provider response exceeded safe size limit"
_RESPONSE_ENCODING_REJECTED_MESSAGE = "provider response used an unsupported content encoding"
_HTTPS_DEFAULT_PORT = 443
_MAX_RESPONSE_BYTES = 25_000_000
# httpx normalizes the host (lowercase, IDNA -> ASCII), so a valid host here
# is plain ASCII labels; anything else (spaces, brackets, percent-escapes) is
# not a hostname this boundary can reason about.
_HOSTNAME_RE = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")

_Origin = tuple[str, str, int]


def _is_relative_path(target: object) -> bool:
    """A rooted, single-slash relative path with no backslash, fragment,
    whitespace or control characters. Pure text check (no parsing) -- it is
    the first of two layers; the built request's parsed origin is verified
    afterwards."""
    return (
        isinstance(target, str)
        and target.startswith("/")
        and not target.startswith("//")
        and "\\" not in target
        and "#" not in target
        and not any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in target)
    )


def _origin_of(url: httpx.URL) -> _Origin:
    """(scheme, host, effective port). httpx lowercases the host and drops a
    default port, so ``https://Host:443`` and ``https://host`` compare equal
    while a different scheme, host or port never does."""
    port = url.port if url.port is not None else _HTTPS_DEFAULT_PORT
    return (url.scheme, url.host, port)


def _parse_base_url(base_url: str) -> httpx.URL:
    """Validate the configured base URL as a plain https origin: https only
    (a cleartext scheme would expose the Cookie), a host, no userinfo, query,
    fragment or path prefix, and a valid port."""
    if not isinstance(base_url, str) or base_url != base_url.strip():
        raise InvalidBaseURLError(_BASE_URL_REJECTED_MESSAGE)
    if "?" in base_url or "#" in base_url:
        raise InvalidBaseURLError(_BASE_URL_REJECTED_MESSAGE)
    try:
        url = httpx.URL(base_url)
    except httpx.InvalidURL:
        raise InvalidBaseURLError(_BASE_URL_REJECTED_MESSAGE) from None
    if url.port is not None and not 1 <= url.port <= 65535:
        raise InvalidBaseURLError(_BASE_URL_REJECTED_MESSAGE)
    if (
        url.scheme != "https"
        or not _HOSTNAME_RE.fullmatch(url.host)
        or url.userinfo
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise InvalidBaseURLError(_BASE_URL_REJECTED_MESSAGE)
    return url


def canonical_origin(base_url: str) -> str:
    """The canonical origin (``https://host`` or ``https://host:port``) of a validated base URL.

    Reuses ``_parse_base_url`` and ``_origin_of`` so there is a single definition of which URLs
    are acceptable and of when two of them are the same origin: scheme, host and effective port
    only. Raises ``InvalidBaseURLError`` for anything the HTTP client itself would reject.
    """
    scheme, host, port = _origin_of(_parse_base_url(base_url))
    return f"{scheme}://{host}" if port == _HTTPS_DEFAULT_PORT else f"{scheme}://{host}:{port}"


class D2LResponse:
    """A minimal, safe wrapper around the provider's JSON response."""

    def __init__(self, status_code: int, json_body: Any) -> None:
        self.status_code = status_code
        self.json_body = json_body


class D2LTextResponse:
    """A minimal, safe wrapper around the provider's TEXT/HTML response.

    Deliberately a separate type from ``D2LResponse`` (see ``get_text()``)
    rather than a union/optional field on one response class -- a caller
    that asks for JSON gets a ``D2LResponse`` with ``json_body``, a caller
    that asks for text gets a ``D2LTextResponse`` with ``text``; there is
    no ambiguous "which attribute is populated this time" case to guess at
    the call site.
    """

    def __init__(self, status_code: int, text: str, content_type: str | None) -> None:
        self.status_code = status_code
        self.text = text
        self.content_type = content_type


class D2LBytesResponse:
    """A bounded binary response (a course file), with its declared media type.

    ``media_type`` is the lower-cased type without parameters. Whether the bytes really are
    that type is the caller's decision (see ``d2l_cli.providers.brightspace_content``).
    """

    def __init__(self, status_code: int, content: bytes, media_type: str) -> None:
        self.status_code = status_code
        self.content = content
        self.media_type = media_type


class D2LHttpClient:
    """GET-only HTTP client for Brightspace API reconnaissance and use.

    Public API surface: ``get()``, ``get_text()``, ``get_bytes()``,
    ``get_text_following_one_redirect()``, and ``close()`` only. Do not add
    ``post``, ``put``, ``patch``, ``delete``, or a generic ``request`` method
    to this class or to anything it returns to callers.
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth_header_provider: AuthHeaderProvider | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._auth_header_provider = auth_header_provider
        base = _parse_base_url(base_url)  # raises InvalidBaseURLError
        self._origin = _origin_of(base)
        # Also used to recognize a same-origin login redirect/interstitial
        # (see d2l_cli.client.session_expiry).
        self._origin_host = base.host
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            verify=True,  # never overridable from the public API
            # Never follow redirects transparently: a redirect (e.g. to an
            # SSO login page on session expiry) must surface as an honest
            # provider-status error (see _check_status), not be silently
            # followed into a 200 response that could then be mistaken for
            # real provider content. This matches httpx's own default, but
            # is pinned explicitly since this behavior is security-relevant.
            follow_redirects=False,
        )

    def get(self, path: str, *, params: Mapping[str, str] | None = None) -> D2LResponse:
        """Issue a GET request and return a normalized JSON response.

        Raises one of the ``d2l_cli.client.errors`` exceptions on failure.
        Never raises with secret material (headers, cookies, tokens) in the
        message. Unchanged Phase 1 contract -- see ``get_text()`` for the
        analogous non-JSON (HTML/text) path.
        """
        response = self._send_get(path, params)
        self._check_status(response)

        try:
            body = response.json()
        except (ValueError, RecursionError):
            raise MalformedResponseError("provider response was not valid JSON") from None

        return D2LResponse(status_code=response.status_code, json_body=body)

    def get_text(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        expected_content_type: str = "text/html",
    ) -> D2LTextResponse:
        """Issue a GET request and return a normalized TEXT/HTML response.

        The Phase 2B analogue of ``get()`` for endpoints that render
        server-side HTML instead of returning JSON (see
        ``d2l_cli.providers.brightspace_assignments``). Shares the exact
        same connection/status handling as ``get()`` (``_send_get`` /
        ``_check_status``) -- GET-only, TLS-verified, no redirects
        followed, identical 401/403/429/5xx/other-status behavior -- so
        this is additive, not a divergent second transport.

        Raises ``MalformedResponseError`` if the response's content-type
        does not contain ``expected_content_type`` -- this is what keeps a
        JSON error envelope or an unrelated response from ever being
        handed to an HTML parser as though it were Assignment HTML. The
        known expired-session shapes (login redirect, or the tiny
        ``200 text/html`` JS-redirect interstitial) are recognized first, in
        ``_check_status``, and raise ``NotAuthenticatedError``. Beyond
        those, this check is necessarily limited to content-type: any other
        same-content-type response that is structurally the wrong PAGE
        cannot be distinguished here without guessing, and is instead the
        caller's (the Assignment parser's) responsibility to reject by
        structure -- see ``ProviderEndpointChangedError`` there.

        Never logs or persists the response body.
        """
        response = self._send_get(path, params)
        self._check_status(response)

        content_type = response.headers.get("content-type", "")
        if expected_content_type not in content_type:
            raise MalformedResponseError(
                f"provider response content-type did not match expected {expected_content_type!r}"
            )

        return D2LTextResponse(
            status_code=response.status_code, text=response.text, content_type=content_type
        )

    def get_bytes(
        self,
        path: str,
        *,
        allowed_media_types: frozenset[str],
        max_bytes: int,
        params: Mapping[str, str] | None = None,
    ) -> D2LBytesResponse:
        """Issue a GET request for a binary document, with its own tighter size bound.

        The same transport as ``get()`` (relative path only, TLS verified, identity encoding,
        no redirects, identical status and expired-session handling); the differences are the
        caller-chosen ``max_bytes`` (enforced while streaming, never above the client-wide
        ceiling) and a media-type allowlist checked on the declared ``Content-Type``. A response
        of any other type is refused without its body being returned. Never logs the body.
        """
        if max_bytes <= 0 or max_bytes > _MAX_RESPONSE_BYTES:
            raise ValueError("max_bytes must be positive and within the client ceiling")
        response = self._send_get(path, params, max_bytes=max_bytes)
        self._check_status(response)
        declared = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if declared not in allowed_media_types:
            raise MalformedResponseError("provider response media type was not an allowed type")
        return D2LBytesResponse(
            status_code=response.status_code, content=response.content, media_type=declared
        )

    def get_text_following_one_redirect(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None,
        validate_redirect: RedirectTargetValidator,
        expected_content_type: str = "text/html",
    ) -> D2LTextResponse:
        """GET ``path``, validate one advertised redirect, then GET its target.

        This is the narrow transport needed by the established Grades route.
        Redirect following remains disabled in httpx. The raw ``Location`` is
        handed only to the caller-supplied validator and is never logged or
        included in an exception. The validator returns a relative path and
        query mapping; the ordinary ``get_text`` path then re-applies the
        request-origin, TLS, authentication, status, session-expiry, and
        content-type boundaries. A second redirect is therefore rejected.

        The callback must raise a safe ``D2LError`` subclass when the target
        does not match its provider-specific contract.
        """
        response = self._send_get(path, params)
        if is_expired_session_response(response, origin_host=self._origin_host):
            raise NotAuthenticatedError(SESSION_EXPIRED_MESSAGE)
        if not 300 <= response.status_code < 400:
            self._check_status(response)
            raise UnexpectedProviderError("provider did not return the expected redirect")
        location = response.headers.get("location")
        if location is None:
            raise UnexpectedProviderError("provider redirect did not include a location")
        redirect_path, redirect_params = validate_redirect(location)
        return self.get_text(
            redirect_path,
            params=redirect_params,
            expected_content_type=expected_content_type,
        )

    def _send_get(
        self,
        path: str,
        params: Mapping[str, str] | None,
        *,
        max_bytes: int | None = None,
    ) -> httpx.Response:
        """Shared request-dispatch logic for ``get()``/``get_text()``:
        attaches auth headers, logs only redacted request metadata (never a
        response body, on either path), and translates transport-level
        failures. Raises before returning if the connection/TLS/timeout
        fails; otherwise returns the raw ``httpx.Response`` for the caller
        to status-check and interpret."""
        # Layer 1: reject anything but a relative path BEFORE the auth
        # header provider is even consulted, before anything is built or
        # logged, and before any network I/O.
        if not _is_relative_path(path):
            raise UnsafeRequestTargetError(_TARGET_REJECTED_MESSAGE)
        # Resolved per call (not as a default) so the client-wide ceiling stays the single source.
        limit = _MAX_RESPONSE_BYTES if max_bytes is None else max_bytes

        headers = dict(self._auth_header_provider()) if self._auth_header_provider else {}
        # Avoid decompression bombs: the response reader bounds identity bytes
        # directly and rejects a server that disregards this request.
        headers["Accept-Encoding"] = "identity"

        try:
            request = self._client.build_request("GET", path, params=params, headers=headers)
        except (httpx.InvalidURL, ValueError):
            raise UnsafeRequestTargetError(_TARGET_REJECTED_MESSAGE) from None
        # Layer 2: the PARSED origin of what will actually be sent must be
        # the configured origin (scheme, host, effective port).
        if _origin_of(request.url) != self._origin:
            raise UnsafeRequestTargetError(_TARGET_REJECTED_MESSAGE)

        # Do not log the URL/path or query values: provider paths can contain
        # student/course identifiers, and even ordinary `ou` values are
        # academic context. Parameter names retain enough structural signal
        # for transport diagnostics without exposing their values.
        logger.debug(
            "GET request param_names=%s headers=%s",
            tuple(sorted((params or {}).keys())),
            redact_headers(headers),
        )

        try:
            response = self._client.send(request, stream=True)
            try:
                content_encoding = response.headers.get("content-encoding", "")
                if content_encoding.strip().lower() not in {"", "identity"}:
                    raise MalformedResponseError(_RESPONSE_ENCODING_REJECTED_MESSAGE)
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    normalized_length = content_length.strip()
                    if (
                        normalized_length.isascii()
                        and normalized_length.isdecimal()
                        and (
                            len(normalized_length) > 20
                            or int(normalized_length) > limit
                        )
                    ):
                        raise MalformedResponseError(_RESPONSE_TOO_LARGE_MESSAGE)

                content = bytearray()
                for chunk in response.iter_bytes():
                    if len(chunk) > limit - len(content):
                        raise MalformedResponseError(_RESPONSE_TOO_LARGE_MESSAGE)
                    content.extend(chunk)

                # The request requires identity encoding, so the bounded bytes
                # are the bytes parsers receive. Remove length/encoding headers
                # when constructing the in-memory response.
                response_headers = [
                    (name, value)
                    for name, value in response.headers.multi_items()
                    if name.lower() not in {"content-length", "content-encoding"}
                ]
                return httpx.Response(
                    status_code=response.status_code,
                    headers=response_headers,
                    content=bytes(content),
                    request=request,
                    extensions=response.extensions,
                )
            finally:
                response.close()
        except httpx.DecodingError:
            raise MalformedResponseError("provider response content could not be decoded") from None
        except httpx.ConnectError as exc:
            if isinstance(exc.__cause__, ssl.SSLError):
                raise TLSVerificationError(
                    "TLS certificate/handshake verification failed"
                ) from None
            raise NetworkUnavailableError(
                f"could not connect to provider: {exc.__class__.__name__}"
            ) from None
        except httpx.TimeoutException as exc:
            raise NetworkUnavailableError(f"request timed out: {exc.__class__.__name__}") from None
        except httpx.TransportError as exc:
            raise NetworkUnavailableError(
                f"provider transport failed: {exc.__class__.__name__}"
            ) from None

    def _check_status(self, response: httpx.Response) -> None:
        """Shared status-code interpretation for ``get()``/``get_text()``.
        Beyond the 401/403/429/5xx handling below (unchanged from Phase 1),
        the observed KSU expired-session shapes -- a 3xx to the same-origin
        login route, or a tiny ``200 text/html`` page that JS-redirects
        there -- are raised as ``NotAuthenticatedError`` (see
        ``d2l_cli.client.session_expiry``). Any OTHER 3xx still falls
        through to ``UnexpectedProviderError``: an honest "something
        unexpected happened," never silently treated as success. Redirects
        are never followed; this is classification only."""
        if is_expired_session_response(response, origin_host=self._origin_host):
            raise NotAuthenticatedError(SESSION_EXPIRED_MESSAGE)
        status = response.status_code
        if status == 401:
            raise NotAuthenticatedError("provider rejected the request as unauthenticated")
        if status == 403:
            raise PermissionDeniedError("provider denied access to this resource")
        if status == 429:
            retry_after = response.headers.get("retry-after")
            retry_after_seconds = (
                int(retry_after)
                if retry_after
                and len(retry_after) <= 10
                and retry_after.isascii()
                and retry_after.isdecimal()
                else None
            )
            raise RateLimitedError(
                "provider rate-limited this request", retry_after_seconds=retry_after_seconds
            )
        if status >= 500:
            raise UnexpectedProviderError(f"provider returned server error status {status}")

        is_success = 200 <= status < 300
        if not is_success:
            raise UnexpectedProviderError(f"provider returned unexpected status {status}")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> D2LHttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
