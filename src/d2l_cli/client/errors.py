"""Error taxonomy for d2l-cli.

Every exception in this module must be safe to print/log: constructors accept
only non-secret data (URLs are allowed but must never carry secrets in their
query string — callers are responsible for that; status codes; short reason
strings). Never pass header values, cookie values, or token contents into
these exceptions.
"""

from __future__ import annotations


class D2LError(Exception):
    """Base class for all d2l-cli errors."""


class NotAuthenticatedError(D2LError):
    """No credential is available at all; the user has not logged in."""


class AuthExpiredError(D2LError):
    """A credential was present but the provider rejected it as expired/invalid."""


class SSOLoginFailedError(D2LError):
    """The interactive SSO/login flow did not complete successfully."""


class NetworkUnavailableError(D2LError):
    """The request could not reach the provider (DNS/connection/timeout)."""


class TLSVerificationError(D2LError):
    """A TLS handshake/certificate error occurred. Never suppressed or bypassed."""


class PermissionDeniedError(D2LError):
    """The provider authenticated the request but denied access (HTTP 403)."""


class RateLimitedError(D2LError):
    """The provider rejected the request due to rate limiting (HTTP 429)."""

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class MalformedResponseError(D2LError):
    """The provider returned a response that could not be parsed as expected."""


class ProviderEndpointChangedError(D2LError):
    """The provider's response shape no longer matches what this client expects.

    Distinct from MalformedResponseError: this indicates the *contract* seems
    to have changed (e.g. expected fields consistently missing), not a single
    bad payload.
    """


class UnexpectedProviderError(D2LError):
    """Catch-all for provider failures that don't fit a more specific category.

    Used instead of silently collapsing everything into a generic failure so
    that unexpected cases remain visible and diagnosable rather than being
    mislabeled as e.g. an auth failure.
    """


class UnsafeRequestTargetError(D2LError):
    """A request target was rejected locally, before any network I/O.

    The HTTP client only accepts RELATIVE Brightspace paths; anything that
    could address another origin (an absolute URL, a scheme-relative target,
    a malformed path) is refused so the session Cookie can never be sent
    off-origin. The message is fixed and never echoes the rejected target.
    Not an auth, permission, outage, or endpoint-drift condition.
    """


class InvalidBaseURLError(D2LError):
    """The configured Brightspace base URL is not a plain https origin, so
    it cannot serve as the request boundary. The message never echoes it."""


class InvalidProfileError(D2LError):
    """An explicit credential profile is missing or unsupported."""


class SecureStorageUnavailableError(D2LError):
    """No trusted OS-backed secure credential store is available.

    Raised instead of ever falling back to writing session material to a
    plaintext file. See docs/PROVIDER_NOTES.md and the Phase 1 design report for the
    Windows Credential Manager (DPAPI-backed) backend this project trusts.
    """


class CredentialReadError(D2LError):
    """A stored credential exists but could not be read/decrypted/parsed.

    Distinct from ``SecureStorageUnavailableError``: the secure store itself
    is reachable, but the specific stored value is corrupt, undecryptable,
    or not in the shape this client expects.
    """


class CredentialAccessError(CredentialReadError):
    """The secure store could not read the selected credential."""


class CredentialPortalMismatchError(CredentialReadError):
    """A v2 record names a different valid canonical portal origin."""


class UnsupportedCredentialVersionError(CredentialReadError):
    """A v2 record uses an unsupported integer format version."""


class UnsupportedPaginationStateError(D2LError):
    """The provider indicated more paginated data exists (a non-null
    ``Bookmark``), but the request semantics for fetching a subsequent page
    were never established during Phase 0.5 reconnaissance (see
    docs/PROVIDER_NOTES.md, "What remains unknown"). Raised instead of guessing a
    bookmark request parameter or silently returning a truncated list.
    """
