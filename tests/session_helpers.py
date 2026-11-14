"""Builds the session store the way the build under test expects (shared test helper)."""

from __future__ import annotations

from d2l_cli.credentials import CredentialStore
from d2l_cli.session_store import SessionStore

TEST_ORIGIN = "https://school.example.edu"


def make_session_store(
    credential_store: CredentialStore, origin: str = TEST_ORIGIN
) -> SessionStore:
    return SessionStore(credential_store, origin=origin)
