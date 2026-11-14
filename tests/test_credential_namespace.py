"""Credential namespace isolation, tested only against fictional in-memory keyrings.

The stored session must live under this project's own Credential Manager service name and must
never read, overwrite, migrate or delete an entry that belongs to any other namespace (another
application, or a differently named build of this tool).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from d2l_cli import credentials
from d2l_cli.client.errors import SecureStorageUnavailableError
from d2l_cli.credentials import KeyringCredentialStore
from d2l_cli.secret import SecretStr
from d2l_cli.session_bridge import SessionMaterial
from d2l_cli.session_store import SessionStore

PUBLIC_SERVICE = "d2l-student-cli"
FOREIGN = ("d2l-cli", "unrelated-app")
FOREIGN_VALUE = "fictional-foreign-entry"


class _Trusted:
    """Stands in for the Windows Credential Manager backend by qualified name."""


_Trusted.__module__ = "keyring.backends.Windows"
_Trusted.__qualname__ = "WinVaultKeyring"


class _Untrusted:
    """Any other backend, for example a plaintext fallback."""


class _Errors:
    class KeyringError(Exception):
        pass

    class PasswordDeleteError(KeyringError):
        pass


class _Keyring:
    """An in-memory keyring that records every operation with its service name."""

    def __init__(self, backend: object | None = None) -> None:
        self.data: dict[tuple[str, str], str] = {}
        self.ops: list[tuple[str, str, str]] = []
        self.fail_set_with: Exception | None = None
        self._backend = backend if backend is not None else _Trusted()
        self.errors = _Errors

    def get_keyring(self) -> object:
        return self._backend

    def set_password(self, service: str, key: str, value: str) -> None:
        self.ops.append(("set", service, key))
        if self.fail_set_with is not None:
            raise self.fail_set_with
        self.data[(service, key)] = value

    def get_password(self, service: str, key: str) -> str | None:
        self.ops.append(("get", service, key))
        return self.data.get((service, key))

    def delete_password(self, service: str, key: str) -> None:
        self.ops.append(("delete", service, key))
        if (service, key) not in self.data:
            raise _Errors.PasswordDeleteError("missing")
        del self.data[(service, key)]


def _store(fake: _Keyring) -> SessionStore:
    module: Any = SimpleNamespace(
        get_keyring=fake.get_keyring,
        set_password=fake.set_password,
        get_password=fake.get_password,
        delete_password=fake.delete_password,
        errors=fake.errors,
    )
    return SessionStore(
        KeyringCredentialStore(keyring_module=module), origin="https://school.example.edu"
    )


def _material() -> SessionMaterial:
    return SessionMaterial(
        cookie_header=SecretStr("fictional=session-value"), cookie_names=("fictional",)
    )


def _seed_foreign(fake: _Keyring) -> None:
    for service in FOREIGN:
        fake.data[(service, "session")] = FOREIGN_VALUE


def test_service_name_is_the_public_namespace() -> None:
    assert credentials._SERVICE_NAME == PUBLIC_SERVICE


def test_save_writes_only_under_the_public_service() -> None:
    fake = _Keyring()
    _store(fake).save(_material())
    assert {service for _, service, _ in fake.ops} == {PUBLIC_SERVICE}
    assert set(fake.data) == {(PUBLIC_SERVICE, "session")}


def test_foreign_entries_are_never_read() -> None:
    fake = _Keyring()
    _seed_foreign(fake)
    assert _store(fake).load() is None
    assert {service for _, service, _ in fake.ops} == {PUBLIC_SERVICE}


def test_foreign_entries_are_never_overwritten_or_deleted() -> None:
    fake = _Keyring()
    _seed_foreign(fake)
    store = _store(fake)
    store.save(_material())
    store.clear()
    for service in FOREIGN:
        assert fake.data[(service, "session")] == FOREIGN_VALUE
    assert (PUBLIC_SERVICE, "session") not in fake.data


def test_no_automatic_migration_or_credential_duplication() -> None:
    fake = _Keyring()
    _seed_foreign(fake)
    store = _store(fake)
    assert store.load() is None
    assert all(op != "set" for op, _, _ in fake.ops)
    store.save(_material())
    stored = fake.data[(PUBLIC_SERVICE, "session")]
    assert FOREIGN_VALUE not in stored
    assert sum(1 for value in fake.data.values() if "session-value" in value) == 1


@pytest.mark.parametrize("service", [*FOREIGN, "", PUBLIC_SERVICE.upper()])
def test_the_service_name_cannot_be_overridden(service: str) -> None:
    with pytest.raises(TypeError):
        KeyringCredentialStore(service_name=service)  # type: ignore[call-arg]


def test_untrusted_backend_is_refused_with_no_plaintext_fallback() -> None:
    fake = _Keyring(backend=_Untrusted())
    with pytest.raises(SecureStorageUnavailableError):
        _store(fake).save(_material())
    assert fake.data == {}
    assert all(op == "get" or op != "set" for op, _, _ in fake.ops)


def test_backend_failures_are_privacy_safe() -> None:
    fake = _Keyring()
    fake.fail_set_with = _Errors.KeyringError("secret-bearing detail: fictional=session-value")
    with pytest.raises(SecureStorageUnavailableError) as raised:
        _store(fake).save(_material())
    assert "session-value" not in str(raised.value)
    assert "secret-bearing" not in str(raised.value)
