from __future__ import annotations

import traceback

import pytest

from d2l_cli.client.errors import CredentialReadError, SecureStorageUnavailableError
from d2l_cli.secret import SecretStr
from d2l_cli.session_bridge import SessionMaterial
from d2l_cli.session_store import SessionStore


class FakeCredentialStore:
    """In-memory CredentialStore for testing SessionStore in isolation from
    any real secure-storage backend."""

    def __init__(self, *, available: bool = True) -> None:
        self._data: dict[str, str] = {}
        self._available = available

    def check_available(self) -> None:
        if not self._available:
            raise SecureStorageUnavailableError("fake backend unavailable")

    def save(self, key: str, value: str) -> None:
        self.check_available()
        self._data[key] = value

    def load(self, key: str) -> str | None:
        self.check_available()
        return self._data.get(key)

    def delete(self, key: str) -> None:
        self.check_available()
        self._data.pop(key, None)


def test_round_trip_through_abstract_credential_store() -> None:
    store = SessionStore(FakeCredentialStore())
    material = SessionMaterial(cookie_header=SecretStr("a=1; b=2"), cookie_names=("a", "b"))
    store.save(material)
    loaded = store.load()
    assert loaded is not None
    assert loaded.cookie_header.reveal() == "a=1; b=2"
    assert loaded.cookie_names == ("a", "b")


def test_load_missing_credential_returns_none() -> None:
    store = SessionStore(FakeCredentialStore())
    assert store.load() is None


def test_load_corrupt_json_raises_credential_read_error() -> None:
    backend = FakeCredentialStore()
    backend.save("session", "not-json-{{{")
    store = SessionStore(backend)
    with pytest.raises(CredentialReadError):
        store.load()


def test_corrupt_session_traceback_does_not_retain_stored_secret() -> None:
    secret = "FICTIONAL_STORED_COOKIE_SECRET"
    backend = FakeCredentialStore()
    backend.save("session", f'{{"cookie_header":"{secret}"')
    store = SessionStore(backend)

    with pytest.raises(CredentialReadError) as exc_info:
        store.load()

    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value), exc_info.value, exc_info.value.__traceback__
        )
    )
    assert secret not in rendered


def test_load_missing_required_field_raises_credential_read_error() -> None:
    backend = FakeCredentialStore()
    backend.save("session", '{"cookie_names": ["a"]}')
    store = SessionStore(backend)
    with pytest.raises(CredentialReadError):
        store.load()


@pytest.mark.parametrize(
    "payload",
    [
        '{"cookie_header":"a=1\\r\\nX-Evil: yes","cookie_names":["a"]}',
        '{"cookie_header":"snowman=☃","cookie_names":["snowman"]}',
        '{"cookie_header":"a=1","cookie_names":"a"}',
        '{"cookie_header":"a=1","cookie_names":[1]}',
        '{"cookie_header":"a=1","cookie_names":["bad;name"]}',
        '{"cookie_header":"","cookie_names":[]}',
    ],
)
def test_load_rejects_malformed_session_fields(payload: str) -> None:
    backend = FakeCredentialStore()
    backend.save("session", payload)
    store = SessionStore(backend)

    with pytest.raises(CredentialReadError, match="stored session material is corrupt"):
        store.load()


def test_load_rejects_oversized_serialized_session_before_json_parsing() -> None:
    backend = FakeCredentialStore()
    backend.save("session", " " * 65_537)
    store = SessionStore(backend)

    with pytest.raises(CredentialReadError, match="stored session material is corrupt"):
        store.load()


def test_storage_backend_unavailable_propagates_from_ensure_available() -> None:
    store = SessionStore(FakeCredentialStore(available=False))
    with pytest.raises(SecureStorageUnavailableError):
        store.ensure_available()


def test_clear_removes_stored_session() -> None:
    backend = FakeCredentialStore()
    store = SessionStore(backend)
    store.save(SessionMaterial(cookie_header=SecretStr("a=1"), cookie_names=("a",)))
    store.clear()
    assert store.load() is None
