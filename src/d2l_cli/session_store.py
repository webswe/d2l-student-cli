"""Persists ``SessionMaterial`` across CLI process invocations.

Only ever writes through a ``CredentialStore`` (OS-protected secure
storage, see ``d2l_cli.credentials``) -- never a plaintext file. This is
what lets ``d2l auth login`` (one process) and a later ``d2l courses
list`` (a separate process) share an authenticated session.
"""

from __future__ import annotations

import json
import re

from d2l_cli.client.errors import CredentialReadError
from d2l_cli.credentials import CredentialStore
from d2l_cli.secret import SecretStr
from d2l_cli.session_bridge import SessionMaterial

_SESSION_KEY = "session"
_MAX_SERIALIZED_SESSION_CHARS = 65_536
_COOKIE_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", re.ASCII)


def _valid_cookie_header(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= _MAX_SERIALIZED_SESSION_CHARS
        and value.isascii()
        and all(0x20 <= ord(character) <= 0x7E for character in value)
    )


def _valid_cookie_names(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(name, str) and _COOKIE_NAME_RE.fullmatch(name) is not None for name in value
        )
    )


class SessionStore:
    def __init__(self, credential_store: CredentialStore) -> None:
        self._store = credential_store

    def ensure_available(self) -> None:
        """Raise ``SecureStorageUnavailableError`` before doing anything
        user-visible (like opening a browser window) if this machine's
        secure storage can't be trusted."""
        self._store.check_available()

    def save(self, material: SessionMaterial) -> None:
        payload = json.dumps(
            {
                "cookie_header": material.cookie_header.reveal(),
                "cookie_names": list(material.cookie_names),
            }
        )
        self._store.save(_SESSION_KEY, payload)

    def load(self) -> SessionMaterial | None:
        raw = self._store.load(_SESSION_KEY)
        if raw is None:
            return None
        if len(raw) > _MAX_SERIALIZED_SESSION_CHARS:
            raise CredentialReadError("stored session material is corrupt: size limit exceeded")
        try:
            data = json.loads(raw)
            cookie_header = data["cookie_header"]
            cookie_names = data["cookie_names"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CredentialReadError(
                f"stored session material is corrupt: {exc.__class__.__name__}"
            ) from None
        if not _valid_cookie_header(cookie_header) or not _valid_cookie_names(cookie_names):
            raise CredentialReadError("stored session material is corrupt: invalid fields")
        return SessionMaterial(
            cookie_header=SecretStr(cookie_header), cookie_names=tuple(cookie_names)
        )

    def clear(self) -> None:
        self._store.delete(_SESSION_KEY)
