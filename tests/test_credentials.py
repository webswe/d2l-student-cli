from __future__ import annotations

import traceback
from types import SimpleNamespace
from typing import Any

import pytest

from d2l_cli.client.errors import CredentialReadError, SecureStorageUnavailableError
from d2l_cli.credentials import KeyringCredentialStore


class _TrustedBackend:
    """Stands in for keyring.backends.Windows.WinVaultKeyring by name."""


_TrustedBackend.__module__ = "keyring.backends.Windows"
_TrustedBackend.__qualname__ = "WinVaultKeyring"


class _UntrustedBackend:
    """Stands in for some other (unevaluated / plaintext-fallback) backend."""


class _FakeKeyringErrors:
    class KeyringError(Exception):
        pass

    class PasswordDeleteError(KeyringError):
        pass


def _make_fake_keyring(
    backend: object,
    *,
    store: dict[str, str] | None = None,
    raise_on_set: Exception | None = None,
    raise_on_get: Exception | None = None,
) -> Any:
    data = store if store is not None else {}

    def get_keyring() -> object:
        return backend

    def set_password(service: str, key: str, value: str) -> None:
        if raise_on_set is not None:
            raise raise_on_set
        data[key] = value

    def get_password(service: str, key: str) -> str | None:
        if raise_on_get is not None:
            raise raise_on_get
        return data.get(key)

    def delete_password(service: str, key: str) -> None:
        if key not in data:
            raise _FakeKeyringErrors.PasswordDeleteError("not found")
        del data[key]

    return SimpleNamespace(
        get_keyring=get_keyring,
        set_password=set_password,
        get_password=get_password,
        delete_password=delete_password,
        errors=_FakeKeyringErrors,
    )


def test_check_available_passes_for_trusted_backend() -> None:
    store = KeyringCredentialStore(keyring_module=_make_fake_keyring(_TrustedBackend()))
    store.check_available()  # should not raise


def test_check_available_raises_for_untrusted_backend() -> None:
    store = KeyringCredentialStore(keyring_module=_make_fake_keyring(_UntrustedBackend()))
    with pytest.raises(SecureStorageUnavailableError):
        store.check_available()


def test_save_and_load_round_trip_on_trusted_backend() -> None:
    fake = _make_fake_keyring(_TrustedBackend())
    store = KeyringCredentialStore(keyring_module=fake)
    store.save("session", "secret-value")
    assert store.load("session") == "secret-value"




def test_load_missing_key_returns_none() -> None:
    store = KeyringCredentialStore(keyring_module=_make_fake_keyring(_TrustedBackend()))
    assert store.load("session") is None


def test_save_refuses_untrusted_backend_without_writing() -> None:
    data: dict[str, str] = {}
    fake = _make_fake_keyring(_UntrustedBackend(), store=data)
    store = KeyringCredentialStore(keyring_module=fake)
    with pytest.raises(SecureStorageUnavailableError):
        store.save("session", "secret-value")
    assert data == {}


def test_save_wraps_backend_error() -> None:
    fake = _make_fake_keyring(
        _TrustedBackend(), raise_on_set=_FakeKeyringErrors.KeyringError("boom")
    )
    store = KeyringCredentialStore(keyring_module=fake)
    with pytest.raises(SecureStorageUnavailableError):
        store.save("session", "secret-value")


def test_load_wraps_backend_error_as_credential_read_error() -> None:
    fake = _make_fake_keyring(
        _TrustedBackend(), raise_on_get=_FakeKeyringErrors.KeyringError("corrupt")
    )
    store = KeyringCredentialStore(keyring_module=fake)
    with pytest.raises(CredentialReadError):
        store.load("session")


@pytest.mark.parametrize("operation", ["save", "load"])
def test_backend_error_traceback_does_not_retain_secret_message(operation: str) -> None:
    secret = "FICTIONAL_CREDENTIAL_BACKEND_SECRET"
    error = _FakeKeyringErrors.KeyringError(secret)
    fake = _make_fake_keyring(
        _TrustedBackend(),
        raise_on_set=error if operation == "save" else None,
        raise_on_get=error if operation == "load" else None,
    )
    store = KeyringCredentialStore(keyring_module=fake)

    with pytest.raises((SecureStorageUnavailableError, CredentialReadError)) as exc_info:
        if operation == "save":
            store.save("session", "fictional-cookie")
        else:
            store.load("session")

    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value), exc_info.value, exc_info.value.__traceback__
        )
    )
    assert secret not in rendered


def test_delete_missing_key_is_a_no_op() -> None:
    store = KeyringCredentialStore(keyring_module=_make_fake_keyring(_TrustedBackend()))
    store.delete("session")  # should not raise


def test_delete_removes_stored_value() -> None:
    data: dict[str, str] = {}
    fake = _make_fake_keyring(_TrustedBackend(), store=data)
    store = KeyringCredentialStore(keyring_module=fake)
    store.save("session", "secret-value")
    store.delete("session")
    assert store.load("session") is None
