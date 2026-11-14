"""Orchestrates browser login, the session bridge, secure persistence, and
authentication-state verification. This is the layer the CLI talks to --
it knows nothing about Playwright or the raw ``manageCourses`` JSON shape.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from d2l_cli.browser_auth import BrowserAuth
from d2l_cli.client.errors import (
    CredentialReadError,
    D2LError,
    NetworkUnavailableError,
    NotAuthenticatedError,
    PermissionDeniedError,
    SecureStorageUnavailableError,
    TLSVerificationError,
)
from d2l_cli.session_bridge import build_session_material
from d2l_cli.session_store import SessionStore

# A callable that exercises a safe, already-authenticated GET request (e.g.
# BrightspaceCourseProvider.list_courses()) against the given headers, and
# raises one of d2l_cli.client.errors on failure. Kept as an injected
# callable so AuthService never needs to know about D2LHttpClient/provider
# wiring -- see cli.py for how it's actually built.
SessionVerifier = Callable[[Mapping[str, str]], None]


class AuthState(StrEnum):
    """Auth status states. "Credential exists in storage" is deliberately
    NOT treated as equivalent to "authenticated" -- AUTHENTICATED requires
    the stored session to be accepted by a live provider request."""

    AUTHENTICATED = "authenticated"
    NOT_AUTHENTICATED = "not_authenticated"
    STORED_SESSION_UNREADABLE = "stored_session_unreadable"
    SESSION_EXPIRED = "session_expired"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


@dataclass(frozen=True)
class AuthStatusResult:
    state: AuthState
    detail: str


class AuthService:
    def __init__(
        self,
        *,
        session_store: SessionStore,
        browser_auth: BrowserAuth,
        verify_session: SessionVerifier,
    ) -> None:
        self._session_store = session_store
        self._browser_auth = browser_auth
        self._verify_session = verify_session

    def login(self, base_url: str) -> None:
        # Fail fast on a bad storage backend *before* opening a browser
        # window -- no point making the user complete SSO/MFA only to
        # discover afterwards that the session can't be saved.
        self._session_store.ensure_available()
        result = self._browser_auth.login(base_url)
        material = build_session_material(result)
        self._session_store.save(material)


    def header_provider(self) -> Mapping[str, str]:
        """Return the headers to attach to GET requests, or raise
        ``NotAuthenticatedError`` if no session is stored.

        Secure-storage and stored-session decoding failures remain ``D2LError``
        subclasses and are handled by the CLI as ordinary, safe application
        failures.
        """
        material = self._session_store.load()
        if material is None:
            raise NotAuthenticatedError("no stored session; run 'd2l auth login' first")
        return {"Cookie": material.cookie_header.reveal()}

    def status(self) -> AuthStatusResult:
        try:
            self._session_store.ensure_available()
        except SecureStorageUnavailableError as exc:
            return AuthStatusResult(AuthState.STORAGE_UNAVAILABLE, str(exc))

        try:
            material = self._session_store.load()
        except CredentialReadError as exc:
            return AuthStatusResult(AuthState.STORED_SESSION_UNREADABLE, str(exc))

        if material is None:
            return AuthStatusResult(
                AuthState.NOT_AUTHENTICATED,
                "no stored session found; run 'd2l auth login'",
            )

        try:
            self._verify_session({"Cookie": material.cookie_header.reveal()})
        except (NotAuthenticatedError, PermissionDeniedError):
            return AuthStatusResult(
                AuthState.SESSION_EXPIRED, "stored session was rejected by the provider"
            )
        except (NetworkUnavailableError, TLSVerificationError) as exc:
            return AuthStatusResult(AuthState.PROVIDER_UNAVAILABLE, f"provider unreachable: {exc}")
        except D2LError as exc:
            return AuthStatusResult(
                AuthState.PROVIDER_UNAVAILABLE, f"provider returned an unexpected error: {exc}"
            )

        return AuthStatusResult(AuthState.AUTHENTICATED, "session verified against provider")
