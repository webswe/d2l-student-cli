from __future__ import annotations

import gzip
import logging
import traceback

import httpx
import pytest
import respx

import d2l_cli.client.http as http_module
from d2l_cli.client.errors import (
    MalformedResponseError,
    NetworkUnavailableError,
    NotAuthenticatedError,
    PermissionDeniedError,
    RateLimitedError,
    UnexpectedProviderError,
)
from d2l_cli.client.http import D2LHttpClient, D2LTextResponse

BASE_URL = "https://example.brightspace.com"


def test_public_api_exposes_only_get_and_lifecycle_methods() -> None:
    # get_text() and the one-redirect Grades helper are intentionally
    # included: both are GET-only and share the same _send_get/_check_status
    # boundary as get(). This test ensures no mutation verb enters the public
    # surface; it does not forbid narrowly scoped GET capabilities.
    public_methods = {
        name
        for name in dir(D2LHttpClient)
        if not name.startswith("_")
    }
    assert public_methods == {
        "get",
        "get_text",
        "get_bytes",
        "get_text_following_one_redirect",
        "close",
    }


def test_client_never_exposes_mutation_verbs() -> None:
    for verb in ("post", "put", "patch", "delete", "request"):
        assert not hasattr(D2LHttpClient, verb)


@respx.mock
def test_get_success_returns_normalized_response() -> None:
    respx.get(f"{BASE_URL}/whoami").mock(
        return_value=httpx.Response(200, json={"Identifier": "123", "UserName": "student1"})
    )
    with D2LHttpClient(BASE_URL) as client:
        response = client.get("/whoami")
    assert response.status_code == 200
    assert response.json_body == {"Identifier": "123", "UserName": "student1"}


@respx.mock
def test_auth_header_provider_is_attached_to_request() -> None:
    route = respx.get(f"{BASE_URL}/whoami").mock(return_value=httpx.Response(200, json={}))
    provider = lambda: {"Authorization": "Bearer abc"}  # noqa: E731
    with D2LHttpClient(BASE_URL, auth_header_provider=provider) as client:
        client.get("/whoami")
    assert route.calls.last.request.headers["Authorization"] == "Bearer abc"
    assert route.calls.last.request.headers["Accept-Encoding"] == "identity"


@respx.mock
def test_get_401_raises_not_authenticated() -> None:
    respx.get(f"{BASE_URL}/whoami").mock(return_value=httpx.Response(401, json={}))
    with D2LHttpClient(BASE_URL) as client, pytest.raises(NotAuthenticatedError):
        client.get("/whoami")


@respx.mock
def test_get_403_raises_permission_denied() -> None:
    respx.get(f"{BASE_URL}/courses").mock(return_value=httpx.Response(403, json={}))
    with D2LHttpClient(BASE_URL) as client, pytest.raises(PermissionDeniedError):
        client.get("/courses")


@respx.mock
def test_get_429_raises_rate_limited_with_retry_after() -> None:
    respx.get(f"{BASE_URL}/courses").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "30"}, json={})
    )
    with D2LHttpClient(BASE_URL) as client:
        with pytest.raises(RateLimitedError) as exc_info:
            client.get("/courses")
    assert exc_info.value.retry_after_seconds == 30


@pytest.mark.parametrize("retry_after", ["9" * 10_000, "tomorrow"])
@respx.mock
def test_untrusted_retry_after_value_cannot_escape_error_taxonomy(
    retry_after: str,
) -> None:
    respx.get(f"{BASE_URL}/courses").mock(
        return_value=httpx.Response(429, headers={"Retry-After": retry_after}, json={})
    )

    with D2LHttpClient(BASE_URL) as client, pytest.raises(RateLimitedError) as exc_info:
        client.get("/courses")

    assert exc_info.value.retry_after_seconds is None


@respx.mock
def test_get_500_raises_unexpected_provider_error() -> None:
    respx.get(f"{BASE_URL}/courses").mock(return_value=httpx.Response(500, text="boom"))
    with D2LHttpClient(BASE_URL) as client, pytest.raises(UnexpectedProviderError):
        client.get("/courses")


@respx.mock
def test_get_malformed_json_raises_malformed_response() -> None:
    respx.get(f"{BASE_URL}/courses").mock(
        return_value=httpx.Response(200, content=b"<html>not json</html>")
    )
    with D2LHttpClient(BASE_URL) as client, pytest.raises(MalformedResponseError):
        client.get("/courses")


@respx.mock
def test_malformed_json_traceback_does_not_retain_response_body() -> None:
    secret = "FICTIONAL_PRIVATE_RESPONSE_VALUE"
    respx.get(f"{BASE_URL}/courses").mock(
        return_value=httpx.Response(200, text=f'{{"private":"{secret}"')
    )

    with D2LHttpClient(BASE_URL) as client, pytest.raises(
        MalformedResponseError
    ) as exc_info:
        client.get("/courses")

    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value), exc_info.value, exc_info.value.__traceback__
        )
    )
    assert secret not in rendered


@respx.mock
def test_excessively_nested_json_fails_as_malformed_response() -> None:
    nested = "[" * 100_000 + "0" + "]" * 100_000
    respx.get(f"{BASE_URL}/courses").mock(
        return_value=httpx.Response(
            200,
            text=nested,
            headers={"content-type": "application/json"},
        )
    )

    with D2LHttpClient(BASE_URL) as client, pytest.raises(
        MalformedResponseError, match="not valid JSON"
    ):
        client.get("/courses")


@respx.mock
def test_malformed_content_encoding_fails_as_safe_provider_error() -> None:
    respx.get(f"{BASE_URL}/courses").mock(
        return_value=httpx.Response(
            200,
            stream=httpx.ByteStream(b"not-a-gzip-stream"),
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
        )
    )

    with D2LHttpClient(BASE_URL) as client, pytest.raises(
        MalformedResponseError, match="could not be decoded"
    ):
        client.get("/courses")


@respx.mock
def test_valid_compressed_response_is_rejected_before_decompression() -> None:
    respx.get(f"{BASE_URL}/courses").mock(
        return_value=httpx.Response(
            200,
            stream=httpx.ByteStream(gzip.compress(b'{"ok":true}')),
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
        )
    )

    with D2LHttpClient(BASE_URL) as client, pytest.raises(
        MalformedResponseError, match="unsupported content encoding"
    ):
        client.get("/courses")


@pytest.mark.parametrize("response_kind", ["json", "text"])
@respx.mock
def test_response_body_over_limit_fails_closed_without_echoing_body(
    monkeypatch: pytest.MonkeyPatch, response_kind: str
) -> None:
    fictional_body = b'{"private":"FICTIONAL_OVERSIZED_ACADEMIC_VALUE"}'
    monkeypatch.setattr(http_module, "_MAX_RESPONSE_BYTES", len(fictional_body) - 1)
    respx.get(f"{BASE_URL}/large").mock(
        return_value=httpx.Response(
            200,
            content=fictional_body,
            headers={
                "content-type": (
                    "application/json" if response_kind == "json" else "text/html"
                )
            },
        )
    )

    with D2LHttpClient(BASE_URL) as client, pytest.raises(MalformedResponseError) as exc_info:
        if response_kind == "json":
            client.get("/large")
        else:
            client.get_text("/large")

    assert "safe size limit" in str(exc_info.value)
    assert "FICTIONAL_OVERSIZED_ACADEMIC_VALUE" not in str(exc_info.value)


@respx.mock
def test_response_body_at_limit_remains_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"ok":true}'
    monkeypatch.setattr(http_module, "_MAX_RESPONSE_BYTES", len(body))
    respx.get(f"{BASE_URL}/bounded").mock(
        return_value=httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/json"},
        )
    )

    with D2LHttpClient(BASE_URL) as client:
        assert client.get("/bounded").json_body == {"ok": True}


@respx.mock
def test_streamed_response_without_length_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b'{"private":"FICTIONAL_STREAMED_VALUE"}'
    monkeypatch.setattr(http_module, "_MAX_RESPONSE_BYTES", len(body) - 1)
    respx.get(f"{BASE_URL}/streamed").mock(
        return_value=httpx.Response(
            200,
            stream=httpx.ByteStream(body),
            headers={"content-type": "application/json"},
        )
    )

    with D2LHttpClient(BASE_URL) as client, pytest.raises(
        MalformedResponseError, match="safe size limit"
    ) as exc_info:
        client.get("/streamed")

    assert "FICTIONAL_STREAMED_VALUE" not in str(exc_info.value)


@respx.mock
def test_get_connect_error_raises_network_unavailable() -> None:
    respx.get(f"{BASE_URL}/courses").mock(side_effect=httpx.ConnectError("refused"))
    with D2LHttpClient(BASE_URL) as client, pytest.raises(NetworkUnavailableError):
        client.get("/courses")


@respx.mock
def test_transport_error_traceback_does_not_retain_secret_message() -> None:
    secret = "FICTIONAL_TRANSPORT_COOKIE_SECRET"
    respx.get(f"{BASE_URL}/courses").mock(side_effect=httpx.ConnectError(secret))

    with D2LHttpClient(BASE_URL) as client, pytest.raises(
        NetworkUnavailableError
    ) as exc_info:
        client.get("/courses")

    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value), exc_info.value, exc_info.value.__traceback__
        )
    )
    assert secret not in rendered


@respx.mock
def test_read_error_is_translated_without_retaining_secret_message() -> None:
    secret = "FICTIONAL_READ_FAILURE_SECRET"
    respx.get(f"{BASE_URL}/courses").mock(side_effect=httpx.ReadError(secret))

    with D2LHttpClient(BASE_URL) as client, pytest.raises(
        NetworkUnavailableError
    ) as exc_info:
        client.get("/courses")

    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value), exc_info.value, exc_info.value.__traceback__
        )
    )
    assert secret not in rendered


@respx.mock
def test_get_never_logs_authorization_header(caplog: pytest.LogCaptureFixture) -> None:
    respx.get(f"{BASE_URL}/whoami").mock(return_value=httpx.Response(200, json={}))
    with caplog.at_level(logging.DEBUG, logger="d2l_cli.http"):
        with D2LHttpClient(
            BASE_URL, auth_header_provider=lambda: {"Authorization": "Bearer super-secret-value"}
        ) as client:
            client.get("/whoami")
    log_text = caplog.text
    assert "super-secret-value" not in log_text
    assert "<REDACTED>" in log_text


# --- get_text() (Phase 2B: HTML transport) ---------------------------------


@respx.mock
def test_get_text_success_returns_normalized_text_response() -> None:
    respx.get(f"{BASE_URL}/d2l/lms/dropbox/user/folders_list.d2l").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<d2l-table-wrapper><table></table></d2l-table-wrapper>",
        )
    )
    with D2LHttpClient(BASE_URL) as client:
        response = client.get_text("/d2l/lms/dropbox/user/folders_list.d2l")
    assert isinstance(response, D2LTextResponse)
    assert response.status_code == 200
    assert "d2l-table-wrapper" in response.text
    assert response.content_type == "text/html; charset=utf-8"


@respx.mock
def test_get_text_passes_params_and_auth_header_like_get() -> None:
    route = respx.get(f"{BASE_URL}/d2l/lms/dropbox/user/folders_list.d2l").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<html/>")
    )
    provider = lambda: {"Cookie": "d2lSessionVal=super-secret"}  # noqa: E731
    with D2LHttpClient(BASE_URL, auth_header_provider=provider) as client:
        client.get_text("/d2l/lms/dropbox/user/folders_list.d2l", params={"ou": "620000"})
    sent = route.calls.last.request
    assert sent.url.params["ou"] == "620000"
    assert sent.headers["Cookie"] == "d2lSessionVal=super-secret"


@respx.mock
def test_get_text_existing_json_get_path_is_unaffected() -> None:
    # Regression: get() must behave identically after the _send_get/
    # _check_status extraction that get_text() reuses.
    respx.get(f"{BASE_URL}/whoami").mock(
        return_value=httpx.Response(200, json={"Identifier": "123"})
    )
    with D2LHttpClient(BASE_URL) as client:
        response = client.get("/whoami")
    assert response.json_body == {"Identifier": "123"}


@respx.mock
def test_get_text_401_raises_not_authenticated() -> None:
    respx.get(f"{BASE_URL}/x").mock(return_value=httpx.Response(401, text=""))
    with D2LHttpClient(BASE_URL) as client, pytest.raises(NotAuthenticatedError):
        client.get_text("/x")


@respx.mock
def test_get_text_403_raises_permission_denied() -> None:
    respx.get(f"{BASE_URL}/x").mock(return_value=httpx.Response(403, text=""))
    with D2LHttpClient(BASE_URL) as client, pytest.raises(PermissionDeniedError):
        client.get_text("/x")


@respx.mock
def test_get_text_500_raises_unexpected_provider_error() -> None:
    respx.get(f"{BASE_URL}/x").mock(return_value=httpx.Response(500, text="boom"))
    with D2LHttpClient(BASE_URL) as client, pytest.raises(UnexpectedProviderError):
        client.get_text("/x")


@respx.mock
def test_get_text_redirect_status_is_not_silently_followed_or_treated_as_success() -> None:
    # A 302 (e.g. to an SSO login page on session expiry) must never be
    # silently followed into an unrelated 200 page nor treated as success --
    # it surfaces as an honest provider-status error, the same as it always
    # has for the JSON path (see _check_status's docstring).
    respx.get(f"{BASE_URL}/x").mock(
        return_value=httpx.Response(302, headers={"location": "https://login.example/sso"})
    )
    with D2LHttpClient(BASE_URL) as client, pytest.raises(UnexpectedProviderError):
        client.get_text("/x")


@respx.mock
def test_get_text_wrong_content_type_raises_malformed_response() -> None:
    # A JSON error envelope (or any non-HTML response) must never be handed
    # to the Assignment HTML parser as though it were Assignment HTML.
    respx.get(f"{BASE_URL}/x").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "application/json"}, json={"error": "nope"}
        )
    )
    with D2LHttpClient(BASE_URL) as client, pytest.raises(MalformedResponseError):
        client.get_text("/x")


@respx.mock
def test_get_text_connect_error_raises_network_unavailable() -> None:
    respx.get(f"{BASE_URL}/x").mock(side_effect=httpx.ConnectError("refused"))
    with D2LHttpClient(BASE_URL) as client, pytest.raises(NetworkUnavailableError):
        client.get_text("/x")


@respx.mock
def test_get_text_never_logs_cookie_header(caplog: pytest.LogCaptureFixture) -> None:
    respx.get(f"{BASE_URL}/x").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<html/>")
    )
    with caplog.at_level(logging.DEBUG, logger="d2l_cli.http"):
        with D2LHttpClient(
            BASE_URL, auth_header_provider=lambda: {"Cookie": "d2lSessionVal=super-secret"}
        ) as client:
            client.get_text("/x")
    log_text = caplog.text
    assert "super-secret" not in log_text
    assert "<REDACTED>" in log_text


@respx.mock
def test_get_text_error_message_never_includes_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx.get(f"{BASE_URL}/x").mock(
        return_value=httpx.Response(500, text="SECRET_SERVER_ERROR_BODY_TEXT")
    )
    with caplog.at_level(logging.DEBUG, logger="d2l_cli.http"):
        with D2LHttpClient(BASE_URL) as client:
            try:
                client.get_text("/x")
            except UnexpectedProviderError as exc:
                assert "SECRET_SERVER_ERROR_BODY_TEXT" not in str(exc)
            else:
                pytest.fail("expected UnexpectedProviderError")
    assert "SECRET_SERVER_ERROR_BODY_TEXT" not in caplog.text
