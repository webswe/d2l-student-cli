"""Persists ``SessionMaterial`` across CLI process invocations, bound to one origin.

Only ever writes through a ``CredentialStore`` (OS-protected secure storage, see
``d2l_cli.credentials``) -- never a plaintext file. This is what lets ``d2l auth login`` (one
process) and a later ``d2l courses list`` (a separate process) share an authenticated session.

Origin binding
--------------
A session is only meaningful for the site that issued it, and the stored value is a ``Cookie``
header that this application attaches itself. So the record carries the canonical origin it was
created for, and ``load()`` refuses to return the cookies unless that origin is *exactly* the
configured one (scheme, host and effective port; see ``canonical_origin``). The check happens
inside ``load()``, before any caller can build a request, so every authenticated command and
``auth status`` are covered by the same code path.

Stored value (JSON, in one Credential Manager entry)::

    {"schema_version": 1, "origin": "https://host[:port]",
     "cookie_header": "...", "cookie_names": ["..."]}

The loaded value is treated as untrusted. Fail-closed rules, none of which ever reveal cookie
text, the stored origin, or the configured origin:

* a record with no origin (written by a pre-release build) is refused
  (``SessionOriginMissingError``);
* an unsupported ``schema_version`` is refused (``UnsupportedCredentialVersionError``);
* a record for a different origin is refused (``CredentialPortalMismatchError``);
* anything else that is not exactly this shape is refused as corrupt (``CredentialReadError``).

A refused record is never repaired, migrated or deleted. A successful ``d2l auth login`` simply
overwrites the entry with a properly bound record.
"""

from __future__ import annotations

import json
import re

from d2l_cli.client.errors import (
    CredentialPortalMismatchError,
    CredentialReadError,
    InvalidBaseURLError,
    SessionOriginMissingError,
    UnsupportedCredentialVersionError,
)
from d2l_cli.client.http import canonical_origin
from d2l_cli.credentials import CredentialStore
from d2l_cli.secret import SecretStr
from d2l_cli.session_bridge import SessionMaterial

_SESSION_KEY = "session"
SCHEMA_VERSION = 1
_MAX_SERIALIZED_SESSION_CHARS = 65_536
_COOKIE_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", re.ASCII)
_RECORD_KEYS = frozenset({"schema_version", "origin", "cookie_header", "cookie_names"})
_LEGACY_KEYS = frozenset({"cookie_header", "cookie_names"})

_RELOGIN = "run 'd2l auth login' to sign in again"
_CORRUPT = f"stored session is corrupt; {_RELOGIN}"
_LEGACY = f"stored session has no recorded site and cannot be reused; {_RELOGIN}"
_VERSION = f"stored session format is not supported; {_RELOGIN}"
_MISMATCH = (
    "stored session was created for a different site than the configured one; "
    f"{_RELOGIN} for this site"
)


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
    """The one stored session, bound to ``origin`` (a validated base URL)."""

    def __init__(self, credential_store: CredentialStore, *, origin: str) -> None:
        self._store = credential_store
        # Raises InvalidBaseURLError for anything the HTTP client itself would reject, so a
        # malformed or unsafe configured site never reaches the credential store.
        self._origin = canonical_origin(origin)

    def ensure_available(self) -> None:
        """Raise ``SecureStorageUnavailableError`` before doing anything
        user-visible (like opening a browser window) if this machine's
        secure storage can't be trusted."""
        self._store.check_available()

    def save(self, material: SessionMaterial) -> None:
        cookie_header = material.cookie_header.reveal()
        cookie_names = list(material.cookie_names)
        if not _valid_cookie_header(cookie_header) or not _valid_cookie_names(cookie_names):
            raise CredentialReadError("session material is invalid")
        payload = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "origin": self._origin,
                "cookie_header": cookie_header,
                "cookie_names": cookie_names,
            }
        )
        if len(payload) > _MAX_SERIALIZED_SESSION_CHARS:
            raise CredentialReadError("session material exceeds the size limit")
        self._store.save(_SESSION_KEY, payload)

    def load(self) -> SessionMaterial | None:
        raw = self._store.load(_SESSION_KEY)
        if raw is None:
            return None
        if not isinstance(raw, str) or len(raw) > _MAX_SERIALIZED_SESSION_CHARS:
            raise CredentialReadError(_CORRUPT)
        try:
            record = json.loads(raw)
        except (ValueError, TypeError):
            raise CredentialReadError(_CORRUPT) from None
        if not isinstance(record, dict):
            raise CredentialReadError(_CORRUPT)
        keys = set(record)
        if keys == _LEGACY_KEYS:
            raise SessionOriginMissingError(_LEGACY)
        if keys != _RECORD_KEYS:
            raise CredentialReadError(_CORRUPT)
        version = record["schema_version"]
        if type(version) is not int:
            raise CredentialReadError(_CORRUPT)
        if version != SCHEMA_VERSION:
            raise UnsupportedCredentialVersionError(_VERSION)
        saved_origin = record["origin"]
        canonical_saved: str | None = None
        if isinstance(saved_origin, str):
            try:
                canonical_saved = canonical_origin(saved_origin)
            except InvalidBaseURLError:
                canonical_saved = None
        if canonical_saved is None or canonical_saved != saved_origin:
            raise CredentialReadError(_CORRUPT)
        # The origin decision comes before the cookie fields are even inspected, and before any
        # caller could build a request: cookies for another origin are never returned.
        if saved_origin != self._origin:
            raise CredentialPortalMismatchError(_MISMATCH)
        cookie_header = record["cookie_header"]
        cookie_names = record["cookie_names"]
        if not _valid_cookie_header(cookie_header) or not _valid_cookie_names(cookie_names):
            raise CredentialReadError(_CORRUPT)
        return SessionMaterial(
            cookie_header=SecretStr(cookie_header), cookie_names=tuple(cookie_names)
        )

    def clear(self) -> None:
        self._store.delete(_SESSION_KEY)
