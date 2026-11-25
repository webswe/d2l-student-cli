from __future__ import annotations

import json

from typer.testing import CliRunner

import d2l_cli.cli as cli
from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.models.content import ContentNode, ContentTree
from d2l_cli.models.course import Course


class Auth:
    def header_provider(self) -> dict[str, str]:
        return {"Cookie": "fictional"}


class Courses:
    def list_courses(self) -> list[Course]:
        return [Course("42", "CS-42", "Structures", "Fall", True, True, False, None, None)]


class Content:
    def tree(self, course_id: str) -> ContentTree:
        assert course_id == "42"
        topic = ContentNode("2", "Reading", "1", "File")
        return ContentTree("42", (ContentNode("1", "<module>", None, "module", (topic,)),), 2)


def setup(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_load_cli_config", lambda: object())
    monkeypatch.setattr(cli, "_build_auth_service", lambda config: Auth())
    monkeypatch.setattr(cli, "_build_course_service", lambda config, headers: Courses())
    monkeypatch.setattr(cli, "_build_content_service", lambda config, headers: Content())


def test_content_tree_json_and_text(monkeypatch) -> None:
    setup(monkeypatch)
    runner = CliRunner()
    good = runner.invoke(cli.app, ["content", "tree", "--course", "42", "--json"])
    assert good.exit_code == 0
    payload = json.loads(good.stdout)["content_tree"]
    assert payload["completeness"] == "unknown" and payload["node_count"] == 2
    assert payload["modules"][0]["children"][0]["parent_module_id"] == "1"
    human = runner.invoke(cli.app, ["content", "toc", "--course", "42"])
    assert human.exit_code == 0 and "Reading" in human.stdout
    assert "completeness is unknown" in human.stderr


def test_registered_commands_and_required_option(monkeypatch) -> None:
    setup(monkeypatch)
    runner = CliRunner()
    content_help = runner.invoke(cli.app, ["content", "--help"])
    assert "tree" in content_help.stdout and "toc" in content_help.stdout
    missing = runner.invoke(cli.app, ["content", "tree", "--json"])
    assert missing.exit_code == 2 and missing.stdout == ""


def test_empty_content_warns_without_claiming_completeness(monkeypatch) -> None:
    setup(monkeypatch)
    monkeypatch.setattr(
        cli, "_build_content_service", lambda config, headers: type(
            "Empty", (), {"tree": lambda self, course_id: ContentTree("42", (), 0)}
        )(),
    )
    result = CliRunner().invoke(cli.app, ["content", "tree", "--course", "42"])
    assert result.exit_code == 0
    assert "No visible content" in result.stdout
    assert "completeness is unknown" in result.stderr


def test_provider_error_leaves_json_stdout_empty(monkeypatch) -> None:
    setup(monkeypatch)

    class BrokenContent:
        def tree(self, course_id: str) -> ContentTree:
            raise ProviderEndpointChangedError("Course content structure is unsupported")

    monkeypatch.setattr(cli, "_build_content_service", lambda config, headers: BrokenContent())
    failed = CliRunner().invoke(cli.app, ["content", "tree", "--course", "42", "--json"])
    assert failed.exit_code == 1 and failed.stdout == ""
    assert "unsupported" in failed.stderr
