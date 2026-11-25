"""Snapshot CLI contract exercised without credentials or network access."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from d2l_cli import cli
from d2l_cli.client.errors import AuthExpiredError
from d2l_cli.snapshot_service import SnapshotService
from tests.test_snapshot_service import Announcements, Assignments, Courses, Grades, course

runner = CliRunner()


class Auth:
    def header_provider(self) -> dict[str, str]:
        return {}


def setup(monkeypatch: pytest.MonkeyPatch, service: SnapshotService) -> None:
    monkeypatch.setattr(cli, "_load_cli_config", lambda: object())
    monkeypatch.setattr(cli, "_build_auth_service", lambda _: Auth())
    monkeypatch.setattr(cli, "_build_snapshot_service", lambda *_: service)


def test_json_envelope_is_stable_and_separates_unrequested_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup(
        monkeypatch,
        SnapshotService(Courses([course("1")]), Assignments(), Grades(), Announcements()),
    )
    result = runner.invoke(cli.app, ["snapshot", "--json", "--sources", "grades"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "schema_version",
        "generated_at",
        "requested_sources",
        "successful_sources",
        "partial",
        "service_calls",
        "http_request_count",
        "courses",
    }
    assert payload["schema_version"] == 1
    assert payload["requested_sources"] == ["grades"]
    assert payload["successful_sources"] == ["grades"]
    assert payload["http_request_count"] is None
    assert payload["courses"][0]["sources"]["assignments"]["status"] == "not_requested"
    assert payload["courses"][0]["grade_items"][0]["displayed_grade"] == "A"
    assert payload["courses"][0]["grade_items"][0]["displayed_points"] is None


def test_partial_json_exits_zero_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    assignments = Assignments()
    assignments.items["1"] = ValueError("private content")
    setup(
        monkeypatch, SnapshotService(Courses([course("1")]), assignments, Grades(), Announcements())
    )
    result = runner.invoke(cli.app, ["snapshot", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["partial"] is True
    assert "private content" not in result.output


def test_all_failed_source_has_json_and_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    assignments = Assignments()
    assignments.items["1"] = ValueError("private content")
    setup(
        monkeypatch, SnapshotService(Courses([course("1")]), assignments, Grades(), Announcements())
    )
    result = runner.invoke(cli.app, ["snapshot", "--json", "--sources", "assignments"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["courses"][0]["sources"]["assignments"]["status"] == "failed"


def test_expired_auth_has_empty_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    assignments = Assignments()
    assignments.items["1"] = AuthExpiredError("session expired")
    setup(
        monkeypatch, SnapshotService(Courses([course("1")]), assignments, Grades(), Announcements())
    )
    result = runner.invoke(cli.app, ["snapshot", "--json"])
    assert result.exit_code == 1
    assert result.stdout == ""


def test_unsupported_source_is_usage_error_before_auth() -> None:
    result = runner.invoke(cli.app, ["snapshot", "--json", "--sources", "posts"])
    assert result.exit_code == 2
    assert result.stdout == ""
