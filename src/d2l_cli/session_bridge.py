"""Browser -> HTTP session bridge.

Converts a ``BrowserAuthResult`` (raw Playwright-observed cookies, already
scoped to the target origin only) into the minimum session material the
GET-only ``D2LHttpClient`` needs: a single ``Cookie`` header value. Nothing
below this module (``D2LHttpClient``, the providers, the CLI) ever depends
on Playwright.

Cookie minimization: Playwright's ``context.cookies(urls=[base_url])``
(see ``d2l_cli.browser_auth``) already filters to cookies applicable to
that origin, which structurally excludes third-party widget cookies (e.g.
the YuJa-issued ``sessionToken``-bearing cookie observed during Phase 0.5
recon -- see docs/PROVIDER_NOTES.md, session 1's "Incident") without guessing or
allowlisting individual cookie *names*.

Phase 0.5 reconnaissance established only that Brightspace requests carry
a single browser-managed ``Cookie`` header (presence only -- individual
cookie names were never enumerated; see docs/PROVIDER_NOTES.md, "What remains
unknown" #3). Because the minimal *named* subset required is not yet
evidenced, this bridge conservatively carries forward every origin-scoped
cookie rather than guessing which subset is required -- narrowing this to
a smaller named allowlist would need a live session to inspect cookie
NAMES (never values), which this phase does not do without explicit
approval. See the Phase 1 report for this limitation.

No localStorage/sessionStorage is read or carried -- Phase 0.5 found no
evidence that Brightspace's GET APIs need anything beyond the Cookie
header (no Authorization header was ever observed; see docs/PROVIDER_NOTES.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from d2l_cli.browser_auth import BrowserAuthResult
from d2l_cli.secret import SecretStr


@dataclass(frozen=True)
class SessionMaterial:
    """The minimum state needed to authenticate GET requests. The cookie
    header value is never exposed via repr()/str()."""

    cookie_header: SecretStr
    cookie_names: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            f"SessionMaterial(cookie_names={list(self.cookie_names)!r}, cookie_header=<REDACTED>)"
        )


def build_session_material(auth_result: BrowserAuthResult) -> SessionMaterial:
    header_value = "; ".join(f"{c.name}={c.value.reveal()}" for c in auth_result.cookies)
    return SessionMaterial(
        cookie_header=SecretStr(header_value),
        cookie_names=tuple(c.name for c in auth_result.cookies),
    )
