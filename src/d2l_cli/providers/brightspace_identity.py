"""GET-only provider for the signed-in user's own Brightspace user id.

Route (documented Valence ``lp`` user API, LP version 1.49 or later; scopes
``users:profile:read`` or ``users:own_profile:read``): ``GET /d2l/api/lp/{version}/users/whoami``.
The response's ``Identifier`` is the user id of the *current session*. Nothing else is trusted:
the id is never taken from a flag, a file, a roster, a link or from another response.

Fail-closed: if the route is unavailable, denied, answers with an unexpected shape, or yields
anything but one positive numeric id, ``IdentityUnverifiedError`` is raised, so data that must
belong to the signed-in user is never shown on a guess. An expired session or a rate limit is
passed through unchanged. The id is held in memory by one provider instance (one command, one
origin) and is never persisted.

Not confirmed against a live tenant: the documentation does not say that ``Identifier`` equals the
``UserId`` found in other APIs (for example quiz attempts), nor that a browser-session cookie is
accepted by this route. If either is false the dependent command refuses to show data.
"""

from __future__ import annotations

from d2l_cli.client.errors import (
    AuthExpiredError,
    D2LError,
    IdentityUnverifiedError,
    NetworkUnavailableError,
    NotAuthenticatedError,
    RateLimitedError,
    TLSVerificationError,
)
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.providers import valence_common as v

_LABEL = "Identity"
LP_MIN_MINOR = 49
LP_MAX_MINOR = 120
IDENTITY_UNVERIFIED_MESSAGE = (
    "the signed-in user's identity could not be verified, so data that must belong to that "
    "user is not shown"
)


def parse_whoami(payload: object) -> str:
    """The one positive numeric user id of a ``WhoAmIUser`` block, or ``ValueError``."""
    if not isinstance(payload, dict):
        raise ValueError("shape")
    identifier = payload.get("Identifier")
    if isinstance(identifier, bool) or not isinstance(identifier, (int, str)):
        raise ValueError("identifier")
    text = str(identifier)
    if not v.is_numeric_id(text) or int(text) <= 0 or text != str(int(text)):
        raise ValueError("identifier")
    return text


class BrightspaceIdentityProvider:
    """Resolve, once per instance, who the current session belongs to."""

    def __init__(self, client: D2LHttpClient) -> None:
        self._client = client
        self._version: str | None = None
        self._user_id: str | None = None

    def _api_version(self) -> str:
        if self._version is None:
            response = self._client.get(v.VERSIONS_PATH)
            self._version = v.select_product_version(
                response.json_body, _LABEL, "lp", LP_MIN_MINOR, LP_MAX_MINOR
            )
        return self._version

    def current_user_id(self) -> str:
        if self._user_id is not None:
            return self._user_id
        try:
            path = f"/d2l/api/lp/{self._api_version()}/users/whoami"
            user_id = parse_whoami(self._client.get(path).json_body)
        except (
            NotAuthenticatedError,
            AuthExpiredError,
            RateLimitedError,
            NetworkUnavailableError,
            TLSVerificationError,
        ):
            raise
        except (D2LError, ValueError):
            raise IdentityUnverifiedError(IDENTITY_UNVERIFIED_MESSAGE) from None
        self._user_id = user_id
        return user_id
