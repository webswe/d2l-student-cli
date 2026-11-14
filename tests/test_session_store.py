"""The origin-bound session store, in isolation from any real secure-storage backend."""

from __future__ import annotations

import json
import traceback

import pytest

from d2l_cli.client.errors import (
    CredentialPortalMismatchError,
    CredentialReadError,
    InvalidBaseURLError,
    SecureStorageUnavailableError,
    SessionOriginMissingError,
    UnsupportedCredentialVersionError,
)
from d2l_cli.secret import SecretStr
from d2l_cli.session_bridge import SessionMaterial
from d2l_cli.session_store import SessionStore

ORIGIN = "https://school.example.edu"
SECRET = "FICTIONAL_STORED_COOKIE_SECRET"


class FakeCredentialStore:
    """In-memory CredentialStore."""

    def __init__(self, *, available: bool = True) -> None:
        self.data: dict[str, str] = {}
        self._available = available

    def check_available(self) -> None:
        if not self._available:
            raise SecureStorageUnavailableError("fake backend unavailable")

    def save(self, key: str, value: str) -> None:
        self.check_available()
        self.data[key] = value

    def load(self, key: str) -> str | None:
        self.check_available()
        return self.data.get(key)

    def delete(self, key: str) -> None:
        self.check_available()
        self.data.pop(key, None)


def _material(header: str = "a=1; b=2", names: tuple[str, ...] = ("a", "b")) -> SessionMaterial:
    return SessionMaterial(cookie_header=SecretStr(header), cookie_names=names)


def _record(**overrides: object) -> str:
    record: dict[str, object] = {
        "schema_version": 1,
        "origin": ORIGIN,
        "cookie_header": "a=1",
        "cookie_names": ["a"],
    }
    record.update(overrides)
    return json.dumps(record)


def _store(backend: FakeCredentialStore, origin: str = ORIGIN) -> SessionStore:
    return SessionStore(backend, origin=origin)


def test_round_trip_for_the_same_origin() -> None:
    store = _store(FakeCredentialStore())
    store.save(_material())
    loaded = store.load()
    assert loaded is not None
    assert loaded.cookie_header.reveal() == "a=1; b=2"
    assert loaded.cookie_names == ("a", "b")


def test_saved_record_is_versioned_and_names_the_canonical_origin() -> None:
    backend = FakeCredentialStore()
    SessionStore(backend, origin="https://School.Example.EDU:443/").save(_material())
    record = json.loads(backend.data["session"])
    assert record == {
        "schema_version": 1,
        "origin": ORIGIN,
        "cookie_header": "a=1; b=2",
        "cookie_names": ["a", "b"],
    }


def test_a_non_default_port_is_part_of_the_stored_origin() -> None:
    backend = FakeCredentialStore()
    SessionStore(backend, origin="https://school.example.edu:8443").save(_material())
    assert json.loads(backend.data["session"])["origin"] == "https://school.example.edu:8443"


def test_load_missing_credential_returns_none() -> None:
    assert _store(FakeCredentialStore()).load() is None


@pytest.mark.parametrize(
    "bad_origin",
    [
        "",
        "http://school.example.edu",
        "https://user:pw@school.example.edu",
        "https://school.example.edu/course",
        "https://school.example.edu?x=1",
        "https://school.example.edu#f",
        " https://school.example.edu",
        "school.example.edu",
        "https://",
    ],
)
def test_construction_rejects_unsafe_origins_before_touching_storage(bad_origin: str) -> None:
    backend = FakeCredentialStore()
    with pytest.raises(InvalidBaseURLError):
        SessionStore(backend, origin=bad_origin)
    assert backend.data == {}


@pytest.mark.parametrize(
    "stored",
    [
        "not-json-{{{",
        "[]",
        '"text"',
        "{}",
        _record(schema_version="1"),
        _record(schema_version=True),
        _record(schema_version=1.0),
        _record(origin=None),
        _record(origin=7),
        _record(origin="https://School.example.edu"),  # not in canonical form
        _record(origin="https://school.example.edu:443"),  # not in canonical form
        _record(origin="https://school.example.edu/"),  # not in canonical form
        _record(origin="http://school.example.edu"),
        _record(origin="javascript:alert(1)"),
        _record(extra="field"),
        _record(cookie_header="a=1\r\nX-Evil: yes"),
        _record(cookie_header="snowman=☃", cookie_names=["snowman"]),
        _record(cookie_header=""),
        _record(cookie_names="a"),
        _record(cookie_names=[1]),
        _record(cookie_names=["bad;name"]),
        _record(cookie_names=[]),
        pytest.param(" " * 65_537, id="oversized"),
        pytest.param("", id="empty"),
    ],
)
def test_load_rejects_corrupt_records(stored: str) -> None:
    backend = FakeCredentialStore()
    backend.data["session"] = stored
    with pytest.raises(CredentialReadError, match="stored session is corrupt"):
        _store(backend).load()


def test_a_record_with_a_missing_field_is_corrupt_not_legacy() -> None:
    backend = FakeCredentialStore()
    backend.data["session"] = '{"schema_version": 1, "origin": "https://school.example.edu"}'
    with pytest.raises(CredentialReadError, match="corrupt"):
        _store(backend).load()


def test_legacy_originless_record_is_refused() -> None:
    backend = FakeCredentialStore()
    backend.data["session"] = json.dumps({"cookie_header": SECRET + "=1", "cookie_names": ["x"]})
    with pytest.raises(SessionOriginMissingError, match="no recorded site") as raised:
        _store(backend).load()
    assert SECRET not in str(raised.value)
    assert backend.data["session"] != ""  # never deleted or rewritten automatically


@pytest.mark.parametrize("version", [0, 2, 99, -1])
def test_unsupported_schema_version_is_refused(version: int) -> None:
    backend = FakeCredentialStore()
    backend.data["session"] = _record(schema_version=version)
    with pytest.raises(UnsupportedCredentialVersionError, match="not supported"):
        _store(backend).load()


@pytest.mark.parametrize(
    "configured",
    [
        "https://other.example.edu",
        "https://sub.school.example.edu",
        "https://example.edu",
        "https://school.example.edu:8443",
        "https://school.example.org",
        "https://school-example.edu",
        "https://school.example.edu.evil.test",
    ],
)
def test_a_session_for_another_origin_is_refused(configured: str) -> None:
    backend = FakeCredentialStore()
    backend.data["session"] = _record(cookie_header=f"{SECRET}=1", cookie_names=[SECRET])
    with pytest.raises(CredentialPortalMismatchError, match="different site") as raised:
        SessionStore(backend, origin=configured).load()
    assert SECRET not in str(raised.value)
    assert configured not in str(raised.value)
    assert ORIGIN not in str(raised.value)


@pytest.mark.parametrize(
    "configured",
    [
        "https://school.example.edu",
        "https://school.example.edu/",
        "https://SCHOOL.example.EDU",
        "https://school.example.edu:443",
        "https://School.Example.Edu:443/",
    ],
)
def test_equivalent_spellings_of_the_same_origin_are_accepted(configured: str) -> None:
    backend = FakeCredentialStore()
    backend.data["session"] = _record()
    loaded = SessionStore(backend, origin=configured).load()
    assert loaded is not None and loaded.cookie_header.reveal() == "a=1"


def test_origin_is_decided_before_cookie_fields_are_inspected() -> None:
    backend = FakeCredentialStore()
    backend.data["session"] = _record(origin="https://other.example.edu", cookie_header="")
    with pytest.raises(CredentialPortalMismatchError):
        _store(backend).load()


def test_errors_and_tracebacks_never_retain_cookie_text() -> None:
    backend = FakeCredentialStore()
    backend.data["session"] = f'{{"schema_version": 1, "cookie_header":"{SECRET}"'
    with pytest.raises(CredentialReadError) as raised:
        _store(backend).load()
    rendered = "".join(
        traceback.format_exception(type(raised.value), raised.value, raised.value.__traceback__)
    )
    assert SECRET not in rendered


def test_save_replaces_a_legacy_record_with_a_bound_one() -> None:
    backend = FakeCredentialStore()
    backend.data["session"] = json.dumps({"cookie_header": "old=1", "cookie_names": ["old"]})
    store = _store(backend)
    with pytest.raises(SessionOriginMissingError):
        store.load()
    store.save(_material("new=2", ("new",)))
    loaded = store.load()
    assert loaded is not None and loaded.cookie_header.reveal() == "new=2"
    assert json.loads(backend.data["session"])["origin"] == ORIGIN


def test_save_rejects_invalid_material() -> None:
    backend = FakeCredentialStore()
    with pytest.raises(CredentialReadError, match="invalid"):
        _store(backend).save(_material("a=1\r\nX: y", ("a",)))
    with pytest.raises(CredentialReadError, match="invalid"):
        _store(backend).save(_material("a=1", ()))
    assert backend.data == {}


def test_storage_backend_unavailable_propagates() -> None:
    store = _store(FakeCredentialStore(available=False))
    with pytest.raises(SecureStorageUnavailableError):
        store.ensure_available()


def test_clear_removes_the_stored_session() -> None:
    backend = FakeCredentialStore()
    store = _store(backend)
    store.save(_material())
    store.clear()
    assert store.load() is None
