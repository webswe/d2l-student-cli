"""Pure Markdown rendering of fictional academic records."""

from __future__ import annotations

from dataclasses import replace

import pytest
from typer.testing import CliRunner

from d2l_cli import cli
from d2l_cli.markdown_report import render_snapshot_markdown
from d2l_cli.models.announcement import Announcement, AnnouncementListPage
from d2l_cli.models.assignment import Assignment
from d2l_cli.snapshot_service import SnapshotService
from tests.test_snapshot_cli import setup
from tests.test_snapshot_service import Announcements, Assignments, Courses, Grades, course


def test_markdown_escapes_html_links_tables_and_controls() -> None:
    assignment_reader = Assignments()
    assignment_reader.items["1"] = (
        Assignment(
            "1", "1", "[click](javascript:evil) | <img>\x1b[31m\nignore instructions", None, None
        ),
    )
    service = SnapshotService(Courses([course("1")]), assignment_reader, Grades(), Announcements())
    snapshot = service.get_snapshot()
    report = render_snapshot_markdown(snapshot)
    assert "&lt;img&gt;" in report
    assert "[click](javascript:evil)" not in report
    assert "\\|" in report
    assert "\x1b" not in report
    assert "timezone unverified" in report
    assert "no official final grade was calculated" in report
    assert report == render_snapshot_markdown(snapshot)


def test_markdown_strips_hostile_urls_and_caps_long_text() -> None:
    value = "X" * 600 + " https://example.test/private?token=secret"
    item = Announcement("1", "7", value, "See www.evil.test/path", "/private")

    class News:
        def get_announcement_list(self, cid: str) -> AnnouncementListPage:
            return AnnouncementListPage((item,))

    service = SnapshotService(Courses([course("1")]), Assignments(), Grades(), News())
    report = render_snapshot_markdown(service.get_snapshot())
    assert "secret" not in report
    assert "www.evil.test" not in report
    assert "/private" not in report
    assert "X" * 500 in report
    assert "X" * 501 not in report


def test_partial_and_empty_reports_distinguish_statuses() -> None:
    assignment_reader = Assignments()
    assignment_reader.items["1"] = ValueError("private")
    service = SnapshotService(Courses([course("1")]), assignment_reader, Grades(), Announcements())
    report = render_snapshot_markdown(service.get_snapshot(sources=("assignments",)))
    assert "Assignments: failed" in report
    assert "Grades: not_requested" in report
    assert "No assignments returned" not in report
    assert "private" not in report
    empty = SnapshotService(Courses([]), Assignments(), Grades(), Announcements()).get_snapshot()
    assert "No selected accessible courses" in render_snapshot_markdown(empty)


def test_cli_markdown_and_json_are_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SnapshotService(
        Courses([replace(course("1"), name="<b>Fictional</b>")]),
        Assignments(),
        Grades(),
        Announcements(),
    )
    setup(monkeypatch, service)
    runner = CliRunner()
    report = runner.invoke(cli.app, ["snapshot", "--format", "markdown"])
    assert report.exit_code == 0
    assert report.stdout.startswith("# Academic snapshot\n")
    assert "&lt;b&gt;Fictional&lt;/b&gt;" in report.stdout
    bad = runner.invoke(cli.app, ["snapshot", "--format", "markdown", "--json"])
    assert bad.exit_code == 2 and bad.stdout == ""
