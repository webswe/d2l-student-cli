from __future__ import annotations

from d2l_cli.client.redact import redact_headers, redact_text, redact_url


def test_redact_headers_hides_authorization() -> None:
    headers = {"Authorization": "Bearer super-secret-token", "Accept": "application/json"}
    redacted = redact_headers(headers)
    assert redacted["Authorization"] == "<REDACTED>"
    assert redacted["Accept"] == "application/json"
    assert "super-secret-token" not in str(redacted)


def test_redact_headers_hides_cookie_case_insensitive() -> None:
    headers = {"cookie": "d2lSessionVal=abc123", "COOKIE": "d2lSessionVal=abc123"}
    redacted = redact_headers(headers)
    assert all(value == "<REDACTED>" for value in redacted.values())


def test_redact_headers_hides_csrf_tokens() -> None:
    headers = {"X-Csrf-Token": "tok-123"}
    redacted = redact_headers(headers)
    assert redacted["X-Csrf-Token"] == "<REDACTED>"


def test_redact_headers_preserves_non_sensitive() -> None:
    headers = {"Content-Type": "application/json", "User-Agent": "d2l-cli/0.1"}
    assert redact_headers(headers) == headers


def test_redact_url_hides_token_query_param() -> None:
    url = "https://example.brightspace.com/d2l/api/lp/1.0/users/whoami?access_token=abcdef"
    redacted = redact_url(url)
    assert "abcdef" not in redacted
    assert "access_token=%3CREDACTED%3E" in redacted or "access_token=<REDACTED>" in redacted


def test_redact_url_preserves_host_and_path() -> None:
    url = "https://example.brightspace.com/d2l/api/lp/1.0/users/whoami?access_token=abcdef"
    redacted = redact_url(url)
    assert redacted.startswith("https://example.brightspace.com/d2l/api/lp/1.0/users/whoami")


def test_redact_url_passthrough_without_query() -> None:
    url = "https://example.brightspace.com/d2l/api/lp/1.0/users/whoami"
    assert redact_url(url) == url


def test_redact_url_hides_vendor_named_session_token_param() -> None:
    # Regression: a third-party widget used "sessionToken" (not an exact
    # match for our old fixed allowlist entries "token"/"session"), and the
    # raw value leaked into a local capture file during Phase 0.5 live
    # reconnaissance before this substring-based matching was added.
    url = "https://widget.example.com/language/en?sessionToken=super-secret-193-chars"
    redacted = redact_url(url)
    assert "super-secret-193-chars" not in redacted


def test_redact_url_hides_apikey_param() -> None:
    url = "https://telemetry.example.com/collect?apikey=abcdef0123456789"
    redacted = redact_url(url)
    assert "abcdef0123456789" not in redacted


def test_redact_url_hides_saml_request_param() -> None:
    url = "https://idp.example.com/saml2?SAMLRequest=base64-encoded-xml-blob"
    redacted = redact_url(url)
    assert "base64-encoded-xml-blob" not in redacted


def test_redact_text_hides_bearer_token() -> None:
    text = "request failed: Authorization: Bearer abc123.def456.ghi789"
    redacted = redact_text(text)
    assert "abc123" not in redacted
    assert "<REDACTED>" in redacted


def test_redact_text_hides_cookie_line() -> None:
    text = "Cookie: d2lSessionVal=super-secret; other=1"
    redacted = redact_text(text)
    assert "super-secret" not in redacted
