"""Secure credential storage abstraction.

Session material (see `d2l_cli.session_bridge`) must never be persisted to
a plaintext file -- see docs/THREAT_MODEL.md. This module is the only place
in the codebase allowed to write session material to disk, and it only
ever does so through the OS's own secure credential store.

``CredentialStore`` is a narrow Protocol so the rest of the application
(``d2l_cli.session_store``, ``d2l_cli.auth_service``) never depends on
``keyring`` directly, and tests can supply an in-memory fake instead of
touching the real OS credential store.

Backend trust: ``keyring`` is a plugin system that can resolve to a wide
range of backends depending on the environment -- including, in some
configurations, unencrypted file-based backends (e.g. from the separate
``keyrings.alt`` package) or a no-op failure backend. Silently writing
through whatever backend happens to be installed would defeat the purpose
of "OS-protected storage only". ``KeyringCredentialStore`` therefore checks
the *resolved* backend's class against an explicit allowlist before every
operation and fails closed (``SecureStorageUnavailableError``) if the
environment doesn't resolve to a backend this project has evaluated and
trusts, rather than assuming any keyring configuration is secure.

Evaluated for this project: on real Windows, ``keyring`` resolves to
``keyring.backends.Windows.WinVaultKeyring``, which persists values via the
Windows Credential Manager API (``CredWrite``/``CredRead``), itself backed
by DPAPI and tied to the current Windows user login -- confirmed by
inspecting ``keyring.get_keyring()`` on this machine. Known failure mode
worth flagging: Windows Credential Manager's generic-credential blob has a
hard size limit (``CRED_MAX_CREDENTIAL_BLOB_SIZE`` = 2560 bytes); if the
serialized session material ever exceeds that, ``set_password`` raises and
this class surfaces it as ``SecureStorageUnavailableError`` rather than
falling back to plaintext. This has not been exercised against a real
Brightspace cookie header yet -- see the Phase 1 report.
"""

from __future__ import annotations

from typing import Any, Protocol

import keyring

from d2l_cli.client.errors import CredentialAccessError, SecureStorageUnavailableError

_SERVICE_NAME = "d2l-student-cli"

# Fully-qualified backend class names this project has evaluated and trusts
# to actually encrypt-at-rest via the OS. Anything else -- including a
# plaintext fallback backend, or no backend at all -- is refused.
_TRUSTED_BACKEND_QUALNAMES = frozenset(
    {
        "keyring.backends.Windows.WinVaultKeyring",
    }
)


class CredentialStore(Protocol):
    """Narrow interface for OS-protected secret storage.

    Deliberately smaller than keyring's own API surface: just enough for
    one named secret to be saved/loaded/deleted, plus an explicit
    availability check callers can run before doing anything user-visible
    (e.g. before opening a browser window for `auth login`).
    """

    def check_available(self) -> None:
        """Raise ``SecureStorageUnavailableError`` if this store cannot be
        trusted to protect secrets in the current environment. No-op
        otherwise."""
        ...

    def save(self, key: str, value: str) -> None: ...

    def load(self, key: str) -> str | None: ...

    def delete(self, key: str) -> None: ...


def _backend_qualname(backend: object) -> str:
    cls = type(backend)
    return f"{cls.__module__}.{cls.__qualname__}"


class KeyringCredentialStore:
    """``CredentialStore`` backed by the OS-native secure store via
    ``keyring``. See module docstring for the trust evaluation."""

    def __init__(self, *, keyring_module: Any = keyring) -> None:
        self._keyring = keyring_module
        self._service_name = _SERVICE_NAME

    def check_available(self) -> None:
        backend = self._keyring.get_keyring()
        qualname = _backend_qualname(backend)
        if qualname not in _TRUSTED_BACKEND_QUALNAMES:
            raise SecureStorageUnavailableError(
                "no trusted OS-backed credential store is available in this "
                f"environment (keyring resolved to {qualname!r}, which is "
                "not in the trusted backend allowlist); refusing to persist "
                "session material rather than fall back to a weaker store"
            )

    def save(self, key: str, value: str) -> None:
        self.check_available()
        try:
            self._keyring.set_password(self._service_name, key, value)
        except self._keyring.errors.KeyringError as exc:
            raise SecureStorageUnavailableError(
                f"failed to write to secure credential storage: {exc.__class__.__name__}"
            ) from None

    def load(self, key: str) -> str | None:
        self.check_available()
        try:
            value: str | None = self._keyring.get_password(self._service_name, key)
        except self._keyring.errors.KeyringError as exc:
            raise CredentialAccessError(
                f"failed to read from secure credential storage: {exc.__class__.__name__}"
            ) from None
        return value

    def delete(self, key: str) -> None:
        self.check_available()
        try:
            self._keyring.delete_password(self._service_name, key)
        except self._keyring.errors.PasswordDeleteError:
            pass
        except self._keyring.errors.KeyringError as exc:
            # Not a SQL query -- ruff's SQL-injection heuristic (S608) fires
            # on the word pair "delete ... from" in this message.
            raise SecureStorageUnavailableError(
                f"secure credential storage removal failed: {exc.__class__.__name__}"  # noqa: S608
            ) from None
