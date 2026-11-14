"""Redaction utilities.

This module must be imported and applied *before* any verbose network
logging is enabled anywhere in the codebase. Nothing outside this module
should format raw headers, cookies, or URLs for logging.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "<REDACTED>"

# Header names whose values must never be logged, case-insensitive.
_SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-csrf-token",
        "x-csrftoken",
        "csrf-token",
        "x-xsrf-token",
        "xsrf-token",
        "proxy-authorization",
        "x-api-key",
    }
)

# Query/body parameter names whose values must never be logged. Matched as a
# case-insensitive SUBSTRING of the parameter name (not exact match): secret
# material shows up under all kinds of vendor-specific names (e.g. a
# third-party widget's "sessionToken" param, or "apikey") that a fixed
# allowlist of exact names will keep missing. Over-redacting a non-secret
# param is an acceptable cost; under-redacting a secret is not.
_SENSITIVE_PARAM_SUBSTRINGS = (
    "token",
    "secret",
    "assertion",
    "password",
    "passwd",
    "session",
    "sid",
    "key",
    "auth",
    "saml",
    "credential",
    "code",
)


def is_sensitive_param_name(name: str) -> bool:
    """Whether a query/form parameter name is known-sensitive (case-insensitive
    substring match against ``_SENSITIVE_PARAM_SUBSTRINGS``).

    Public for the same reason ``is_sensitive_header_name`` is: other
    tooling (e.g. ``scripts/recon_lib.py``'s HTML structural sanitizer)
    needs the identical classification other code in this module uses, so
    it should import this rather than keep a second, divergent list (see
    the module docstring's "must be imported... before any verbose network
    logging" note and docs/PROVIDER_NOTES.md's session-1 incident, where a
    divergent list let a sensitive value slip through unredacted).
    """
    lowered = name.lower()
    return any(substring in lowered for substring in _SENSITIVE_PARAM_SUBSTRINGS)


def is_sensitive_header_name(name: str) -> bool:
    """Whether a header name is known-sensitive (case-insensitive exact match).

    Public so other tooling (e.g. ``scripts/recon_capture.py``) can classify
    headers the same way this module does, rather than maintaining a second,
    divergent list of sensitive header names.
    """
    return name.lower() in _SENSITIVE_HEADER_NAMES


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with sensitive values replaced."""
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if is_sensitive_header_name(key):
            redacted[key] = REDACTED
        else:
            redacted[key] = value
    return redacted


def redact_url(url: str) -> str:
    """Return ``url`` with sensitive query-string values replaced.

    Scheme, host, and path are preserved (hosts are not secret and are useful
    for diagnostics); only query parameter *values* whose parameter name
    looks secret-shaped (see ``_SENSITIVE_PARAM_SUBSTRINGS``) are redacted.
    Parameter names are matched by substring, not exact name, since secret
    material shows up under many vendor-specific param names.
    """
    parts = urlsplit(url)
    if not parts.query:
        return url

    redacted_pairs = [
        (key, REDACTED if is_sensitive_param_name(key) else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    new_query = urlencode(redacted_pairs)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def redact_text(text: str) -> str:
    """Best-effort redaction of secret-shaped substrings in free-form text.

    This is a safety net for exception messages / log lines that embed a
    header-like fragment (e.g. ``"Authorization: Bearer abcd..."``) rather
    than passing structured headers through ``redact_headers``. It is not a
    substitute for structured redaction.
    """
    import re

    patterns = [
        (re.compile(r"(?i)(bearer)\s+[A-Za-z0-9\-._~+/]+=*"), r"\1 " + REDACTED),
        (re.compile(r"(?i)(authorization\s*:\s*)\S+"), r"\1" + REDACTED),
        (re.compile(r"(?i)(cookie\s*:\s*).+"), r"\1" + REDACTED),
        (re.compile(r"(?i)(set-cookie\s*:\s*).+"), r"\1" + REDACTED),
    ]
    result = text
    for pattern, replacement in patterns:
        result = pattern.sub(replacement, result)
    return result
