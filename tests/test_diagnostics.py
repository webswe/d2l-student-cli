"""Offline diagnostics never read credentials or contact the provider."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from d2l_cli import cli
from d2l_cli import diagnostics as diagnostics_module
from d2l_cli.diagnostics import diagnostics_report
from d2l_cli.snapshot_service import SnapshotService
from tests.test_snapshot_cli import setup
from tests.test_snapshot_service import Announcements, Assignments, Courses, Grades, course


def test_report_schema_and_local_only_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli, "_build_auth_service", lambda *_: (_ for _ in ()).throw(AssertionError())
    )
    report = diagnostics_report()
    assert set(report) == {
        "schema_version",
        "application_version",
        "python_version",
        "configuration_valid",
        "configuration_error_code",
        "dependencies",
        "local_capabilities",
        "session_presence",
        "provider_verification",
        "known_limitations",
    }
    assert report["schema_version"] == 1
    assert report["session_presence"] == "not_checked"
    assert report["provider_verification"] == "not_performed"
    assert set(report["dependencies"]) == {"certifi", "httpx", "keyring", "playwright", "typer"}


def test_invalid_configuration_is_safe_and_does_not_echo_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("D2L_CLI_BASE_URL", "https://user:private@bad.example/path")
    result = CliRunner().invoke(cli.app, ["diagnostics", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["configuration_valid"] is False
    assert payload["configuration_error_code"] == "invalid_base_url"
    assert "private" not in result.output
    assert "bad.example" not in result.output


def test_text_and_json_modes_have_separate_output() -> None:
    runner = CliRunner()
    text = runner.invoke(cli.app, ["diagnostics"])
    machine = runner.invoke(cli.app, ["diagnostics", "--json"])
    assert text.exit_code == machine.exit_code == 0
    assert "Session presence: not checked" in text.stdout
    assert machine.stdout.startswith('{"schema_version": 1,')
    assert text.stderr == machine.stderr == ""


def test_missing_optional_dependency_is_reported_without_importing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = diagnostics_module.importlib.util.find_spec

    def find_spec(name: str) -> object:
        return None if name == "playwright" else original(name)

    monkeypatch.setattr(diagnostics_module.importlib.util, "find_spec", find_spec)
    report = diagnostics_report()
    assert report["dependencies"]["playwright"] is False
    assert report["provider_verification"] == "not_performed"


def test_diagnostics_avoids_auth_session_and_network_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("diagnostics accessed an authenticated boundary")

    for name in (
        "_build_auth_service",
        "_build_snapshot_service",
        "SessionStore",
        "KeyringCredentialStore",
        "D2LHttpClient",
    ):
        monkeypatch.setattr(cli, name, forbidden)
    result = CliRunner().invoke(cli.app, ["diagnostics", "--json"])
    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout)["session_presence"] == "not_checked"


def test_snapshot_markdown_and_diagnostics_coexist(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SnapshotService(Courses([course("1")]), Assignments(), Grades(), Announcements())
    setup(monkeypatch, service)
    runner = CliRunner()
    snapshot = runner.invoke(cli.app, ["snapshot", "--json"])
    markdown = runner.invoke(cli.app, ["snapshot", "--format", "markdown"])
    diagnostic = runner.invoke(cli.app, ["diagnostics", "--json"])
    assert snapshot.exit_code == markdown.exit_code == diagnostic.exit_code == 0
    assert json.loads(snapshot.stdout)["schema_version"] == 1
    assert markdown.stdout.startswith("# Academic snapshot\n")
    assert json.loads(diagnostic.stdout)["schema_version"] == 1
    assert snapshot.stderr == markdown.stderr == diagnostic.stderr == ""


def test_help_and_invalid_options_preserve_exit_codes() -> None:
    runner = CliRunner()
    for args in (["snapshot", "--help"], ["diagnostics", "--help"], ["overview", "--help"]):
        result = runner.invoke(cli.app, args)
        assert result.exit_code == 0
    invalid = runner.invoke(cli.app, ["diagnostics", "--unexpected"])
    assert invalid.exit_code == 2
    assert invalid.stdout == ""
