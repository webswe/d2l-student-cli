"""Cross-command regression tests for the external machine interface."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from typer.testing import CliRunner

import d2l_cli.cli as cli_module
from d2l_cli.cli import app
from d2l_cli.client.errors import (
    CredentialReadError,
    D2LError,
    SecureStorageUnavailableError,
)
from d2l_cli.models.course import Course


class _FailingSessionAuth:
    def __init__(self, error: D2LError) -> None:
        self._error = error

    def header_provider(self) -> Mapping[str, str]:
        raise self._error


class _AuthenticatedSessionAuth:
    def header_provider(self) -> Mapping[str, str]:
        return {"Cookie": "fictional-session-value"}


class _CourseService:
    def __init__(self, courses: list[Course]) -> None:
        self._courses = courses

    def list_courses(self) -> list[Course]:
        return self._courses


@pytest.mark.parametrize(
    "arguments",
    [
        ("assignments", "list", "--json"),
        ("grades", "list", "--json"),
        ("announcements", "list", "--json"),
    ],
)
def test_missing_required_course_is_usage_error_with_no_json(
    arguments: tuple[str, ...],
) -> None:
    result = CliRunner().invoke(app, list(arguments))

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Missing option '--course'" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ("courses", "list", "--json"),
        ("assignments", "list", "--course", "123456", "--json"),
        ("assignments", "overview", "--json"),
        ("grades", "list", "--course", "123456", "--json"),
        ("announcements", "list", "--course", "123456", "--json"),
        ("overview", "--json"),
    ],
)
@pytest.mark.parametrize(
    ("error_type", "message"),
    [
        (CredentialReadError, "stored session material is corrupt: JSONDecodeError"),
        (SecureStorageUnavailableError, "secure credential storage is unavailable"),
    ],
)
def test_session_storage_failure_is_stable_nonzero_error_with_empty_stdout(
    monkeypatch: pytest.MonkeyPatch,
    arguments: tuple[str, ...],
    error_type: type[D2LError],
    message: str,
) -> None:
    """Every JSON command applies the same safe pre-provider failure contract."""
    error = error_type(message)
    monkeypatch.setattr(
        cli_module,
        "_build_auth_service",
        lambda _config: _FailingSessionAuth(error),
    )

    result = CliRunner().invoke(app, list(arguments))

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"error: {message}\n"
    assert result.exception is not None
    assert not isinstance(result.exception, D2LError)


def test_large_unicode_course_result_preserves_schema_and_provider_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    courses = [
        Course(
            id=str(700000 + index),
            code=f"FIC-{index:04d}",
            name=f"Fictional Étude 🚀 {index}",
            semester_name="Fictional Fall",
            is_active=True,
            can_access=True,
            is_new_enrollment=False,
            start_date=None if index % 2 else "2026-08-01T00:00:00.000Z",
            end_date=None,
        )
        for index in range(2048)
    ]
    monkeypatch.setattr(
        cli_module,
        "_build_auth_service",
        lambda _config: _AuthenticatedSessionAuth(),
    )
    monkeypatch.setattr(
        cli_module,
        "_build_course_service",
        lambda _config, _headers: _CourseService(courses),
    )

    result = CliRunner().invoke(app, ["courses", "list", "--json"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert len(result.stdout.encode("utf-8")) > 250_000
    payload = json.loads(result.stdout)
    assert len(payload["courses"]) == 2048
    assert payload["courses"][0]["id"] == "700000"
    assert payload["courses"][0]["name"] == "Fictional Étude 🚀 0"
    assert payload["courses"][-1]["id"] == "702047"
