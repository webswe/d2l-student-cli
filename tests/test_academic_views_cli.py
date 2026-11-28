"""Cross-course academic views: fictional fixtures, no credentials, no network."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from typer.testing import CliRunner

from d2l_cli import cli
from d2l_cli.academic_views import (
    announcements_overview_json,
    assignments_dashboard_json,
    grades_overview_json,
    render_view_markdown,
    render_view_text,
    student_overview_json,
)
from d2l_cli.client.errors import AuthExpiredError, ProviderEndpointChangedError
from d2l_cli.course_resolver import CourseResolutionError
from d2l_cli.models.announcement import Announcement, AnnouncementListPage
from d2l_cli.models.assignment import Assignment
from d2l_cli.models.assignment_completeness import (
    AssignmentCompleteness,
    AssignmentCompletenessStatus,
    AssignmentListPage,
)
from d2l_cli.models.course import Course
from d2l_cli.models.grade import FeedbackAvailability, GradeItem, GradeListPage
from d2l_cli.snapshot_service import SnapshotService

runner = CliRunner()


def course(
    cid: str, code: str | None = None, *, active: bool = True, access: bool = True
) -> Course:
    return Course(
        cid,
        code or f"FIC{cid}",
        f"Fictional Course {cid}",
        "Fall",
        active,
        access,
        False,
        None,
        None,
    )


class Courses:
    def __init__(self, values: list[Course]) -> None:
        self.values = values
        self.calls = 0

    def list_courses(self) -> list[Course]:
        self.calls += 1
        return self.values


class Reader:
    """One configurable fake serving all three sources."""

    def __init__(self) -> None:
        self.assignments: dict[str, tuple[Assignment, ...] | Exception] = {}
        self.grades: dict[str, tuple[GradeItem, ...] | Exception] = {}
        self.announcements: dict[str, tuple[Announcement, ...] | Exception] = {}
        self.calls: list[tuple[str, str]] = []

    def get_assignment_list(self, cid: str) -> AssignmentListPage:
        self.calls.append(("assignments", cid))
        value = self.assignments.get(cid, ())
        if isinstance(value, Exception):
            raise value
        return AssignmentListPage(
            value, AssignmentCompleteness(AssignmentCompletenessStatus.UNKNOWN, len(value), None)
        )

    def get_grade_list(self, cid: str) -> GradeListPage:
        self.calls.append(("grades", cid))
        value = self.grades.get(cid, ())
        if isinstance(value, Exception):
            raise value
        return GradeListPage(value, 0, 2, False)

    def get_announcement_list(self, cid: str) -> AnnouncementListPage:
        self.calls.append(("announcements", cid))
        value = self.announcements.get(cid, ())
        if isinstance(value, Exception):
            raise value
        return AnnouncementListPage(value)


def grade(
    cid: str, label: str, g: str | None = None, p: str | None = None, w: str | None = None
) -> GradeItem:
    return GradeItem(cid, label, p, g, w, FeedbackAvailability.UNKNOWN)


class Auth:
    def header_provider(self) -> dict[str, str]:
        return {}


def build(values: list[Course]) -> tuple[SnapshotService, Courses, Reader]:
    courses = Courses(values)
    reader = Reader()
    return SnapshotService(courses, reader, reader, reader), courses, reader


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[Courses, Reader]:
    service, courses, reader = build(
        [course("2", "BIO200"), course("1", "ART100"), course("3", "OLD", active=False)]
    )
    monkeypatch.setattr(cli, "_load_cli_config", lambda: object())
    monkeypatch.setattr(cli, "_build_auth_service", lambda _: Auth())
    monkeypatch.setattr(cli, "_build_snapshot_service", lambda *_: service)
    return courses, reader


def run(*args: str) -> tuple[int, str, str]:
    result = runner.invoke(cli.app, list(args))
    return result.exit_code, result.stdout, result.stderr


# ----------------------------------------------------------------------- grades


def test_grades_overview_preserves_provider_order_and_duplicates(
    wired: tuple[Courses, Reader],
) -> None:
    _, reader = wired
    reader.grades["1"] = (
        grade("1", "Zed", "A"),
        grade("1", "Quiz", "B"),
        grade("1", "Quiz", "B"),
        grade("1", "Blank"),
    )
    code, out, _ = run("grades", "overview", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["schema_version"] == 1 and payload["kind"] == "grades_overview"
    assert payload["official_final_grade_calculated"] is False
    assert payload["provider_totals_verified"] is False
    assert [c["course"]["id"] for c in payload["courses"]] == ["1", "2"]  # inactive 3 excluded
    items = payload["courses"][0]["grade_items"]
    assert [i["label"] for i in items] == ["Zed", "Quiz", "Quiz", "Blank"]
    assert [i["index"] for i in items] == [0, 1, 2, 3]
    assert items[3]["displayed_grade"] is None  # blank is not zero
    assert payload["courses"][0]["grade_item_count"] == 4


def test_grades_failure_is_not_empty(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    reader.grades["1"] = ProviderEndpointChangedError("https://private.example/x?token=secret")
    code, out, err = run("grades", "overview", "--json")
    assert code == 0  # partial: course 2 succeeded
    payload = json.loads(out)
    failed, ok = payload["courses"]
    assert failed["source"]["status"] == "failed" and failed["grade_item_count"] is None
    assert ok["source"]["status"] == "success" and ok["grade_item_count"] == 0
    assert payload["partial"] is True
    assert "secret" not in out + err and "private.example" not in out + err
    assert "warning" in err


def test_all_sources_failed_exits_nonzero(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    reader.grades["1"] = ProviderEndpointChangedError("x")
    reader.grades["2"] = ValueError("y")
    code, out, _ = run("grades", "overview", "--json")
    assert code == 1
    assert json.loads(out)["partial"] is True


def test_wrong_course_association_is_a_failed_source_not_data(
    wired: tuple[Courses, Reader],
) -> None:
    _, reader = wired
    reader.grades["1"] = (grade("2", "Leak"),)
    payload = json.loads(run("grades", "overview", "--json")[1])
    assert payload["courses"][0]["source"]["status"] == "failed"
    assert payload["courses"][0]["grade_items"] == []


def test_auth_expiry_is_fatal_and_not_swallowed(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    reader.grades["1"] = AuthExpiredError("expired")
    code, out, err = run("grades", "overview", "--json")
    assert code == 1 and out == "" and "error:" in err


def test_human_grades_has_no_average(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    reader.grades["1"] = (grade("1", "Quiz", "A", "9/10", "10%"),)
    code, out, _ = run("grades", "overview")
    assert code == 0
    assert "ART100" in out and "grade: A" in out and "points: 9/10" in out
    assert "no official final grade or average was calculated" in out


# ------------------------------------------------------------------ assignments


def test_dashboard_sort_filter_and_unknown_dates(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    reader.assignments["1"] = (
        Assignment("10", "1", "Undated A", None, None),
        Assignment("11", "1", "Late", "Submitted", "9/10", datetime(2026, 12, 5, 23, 59)),
    )
    reader.assignments["2"] = (
        Assignment("20", "2", "Early", None, None, datetime(2026, 12, 1, 8, 0)),
        Assignment("21", "2", "Tie", None, None, datetime(2026, 12, 5, 23, 59)),
        Assignment("22", "2", "Undated B", None, None),
    )
    payload = json.loads(run("assignments", "dashboard", "--json", "--sort", "due")[1])
    names = [i["name"] for i in payload["items"]]
    assert names == ["Early", "Late", "Tie", "Undated A", "Undated B"]  # ties/unknown keep order
    assert payload["observed_assignment_count"] == 5
    assert all("overdue" not in k and "status_class" not in k for k in payload["items"][0])
    assert (
        payload["items"][3]["due_date"] is None
        and payload["items"][3]["due_date_recognized"] is False
    )
    assert "not that there is no deadline" in payload["due_date_note"]

    provider = json.loads(run("assignments", "dashboard", "--json")[1])
    assert [i["name"] for i in provider["items"]] == [
        "Undated A",
        "Late",
        "Early",
        "Tie",
        "Undated B",
    ]

    unrec = json.loads(run("assignments", "dashboard", "--json", "--due-date", "unrecognized")[1])
    assert [i["name"] for i in unrec["items"]] == ["Undated A", "Undated B"]
    assert unrec["observed_assignment_count"] == 5 and unrec["returned_item_count"] == 2
    rec = json.loads(run("assignments", "dashboard", "--json", "--due-date", "recognized")[1])
    assert len(rec["items"]) == 3


def test_dashboard_text_never_says_no_deadline(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    reader.assignments["1"] = (Assignment("10", "1", "Undated", None, None),)
    out = run("assignments", "dashboard")[1]
    assert "unknown (no recognized date)" in out
    assert "no deadline" not in out.replace("not that there is no deadline", "")
    assert "overdue" not in out.lower()


def test_dashboard_failed_course_listed_not_empty(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    reader.assignments["1"] = ValueError("boom")
    code, out, err = run("assignments", "dashboard")
    assert code == 0
    assert "read failed" in out and "not the same as empty" in out
    assert "warning" in err


# ---------------------------------------------------------------- announcements


def test_announcements_bounded_sanitized_no_detail_reference(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    reader.announcements["1"] = tuple(
        Announcement(
            "1",
            str(100 - i),
            f"T{i}\x1b[31m",
            "B" * 1000 if i == 0 else None,
            "/d2l/le/news/1/1/view",
        )
        for i in range(5)
    )
    payload = json.loads(run("announcements", "overview", "--json", "--items-per-course", "2")[1])
    c = payload["courses"][0]
    assert [a["announcement_id"] for a in c["announcements"]] == [
        "100",
        "99",
    ]  # provider order kept
    assert c["observed_announcement_count"] == 5 and c["omitted_by_item_limit"] == 3
    assert c["announcements"][0]["body_preview_truncated"] is True
    assert len(c["announcements"][0]["body_preview"]) <= 300
    assert "\x1b" not in json.dumps(payload) and "detail_reference" not in json.dumps(payload)
    assert "/d2l/le/news" not in json.dumps(payload)
    assert payload["chronological_order_verified"] is False


def test_announcements_empty_vs_failed(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    reader.announcements["2"] = ValueError("x")
    out = run("announcements", "overview")[1]
    assert "No announcements returned; total completeness is unverified." in out
    assert "read failed" in out
    assert "not a newest-first feed" in out


def test_items_per_course_out_of_range_is_usage_error(wired: tuple[Courses, Reader]) -> None:
    courses, _ = wired
    code, _, err = run("announcements", "overview", "--items-per-course", "0")
    assert code == 2 and "items-per-course" in err and courses.calls == 0


# ------------------------------------------------------------------ student


def test_student_overview_counts_are_observed_only(wired: tuple[Courses, Reader]) -> None:
    courses, reader = wired
    reader.assignments["1"] = (
        Assignment("1", "1", "A", None, None, datetime(2026, 12, 1, 8)),
        Assignment("2", "1", "B", None, None),
    )
    reader.grades["2"] = (grade("2", "Q"), grade("2", "Q"))
    reader.announcements["2"] = ValueError("x")
    payload = json.loads(run("student", "overview", "--json")[1])
    assert (
        payload["counts_are_observed_only"] is True and payload["provider_totals_verified"] is False
    )
    t = payload["totals"]
    assert (t["observed_assignment_count"], t["recognized_due_date_count"]) == (2, 1)
    assert t["observed_grade_item_count"] == 2 and t["observed_announcement_count"] == 0
    assert payload["sources"]["announcements"] == {"courses_succeeded": 1, "courses_failed": 1}
    assert payload["partial"] is True
    assert payload["courses"][1]["observed_announcement_count"] is None
    assert courses.calls == 1  # one course list for the whole overview
    assert len(reader.calls) == 6  # 2 courses x 3 sources, no duplicate reads


# ------------------------------------------------------------ course selection


def test_course_selection_by_code_and_numeric_id(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    payload = json.loads(
        run("grades", "overview", "--json", "--course", "bio200", "--course", "2")[1]
    )
    assert [c["course"]["id"] for c in payload["courses"]] == ["2"]  # deduped
    assert reader.calls == [("grades", "2")]


@pytest.mark.parametrize("query", ["nonexistent", "3", "Fictional"])
def test_unknown_inaccessible_or_ambiguous_course_fails_before_reads(
    wired: tuple[Courses, Reader], query: str
) -> None:
    _, reader = wired
    code, out, err = run("grades", "overview", "--json", "--course", query)
    assert code == 1 and out == "" and "error:" in err
    assert reader.calls == []


@pytest.mark.parametrize("bad", ["", "   ", "x\x1b[2J", "a" * 201])
def test_invalid_course_query_rejected_before_credentials(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    def boom() -> None:
        raise AssertionError("credentials/config must not be touched")

    monkeypatch.setattr(cli, "_load_cli_config", boom)
    code, _, err = run("grades", "overview", "--course", bad)
    assert code == 2 and "--course value is invalid" in err


def test_max_courses_enforced_before_course_reads(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    code, _, err = run("grades", "overview", "--max-courses", "1")
    assert code == 1 and reader.calls == []
    assert "max_courses" in err or "invalid" in err
    code, _, err = run("grades", "overview", "--max-courses", "21")
    assert code == 2


def test_option_validation_precedes_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli, "_load_cli_config", lambda: (_ for _ in ()).throw(AssertionError("no"))
    )
    for args in (
        ["grades", "overview", "--format", "html"],
        ["grades", "overview", "--json", "--format", "markdown"],
        ["assignments", "dashboard", "--sort", "newest"],
        ["assignments", "dashboard", "--due-date", "overdue"],
    ):
        assert run(*args)[0] == 2


def test_all_courses_includes_inactive(wired: tuple[Courses, Reader]) -> None:
    payload = json.loads(run("grades", "overview", "--json", "--all-courses")[1])
    assert [c["course"]["id"] for c in payload["courses"]] == ["1", "2", "3"]


def test_stdout_stderr_separation(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    reader.grades["1"] = ValueError("x")
    result = runner.invoke(cli.app, ["grades", "overview", "--json"])
    json.loads(result.stdout)  # stdout is pure JSON
    assert "warning" in result.stderr and "warning" not in result.stdout


# ------------------------------------------------------------ rendering / injection

HOSTILE = "<script>alert(1)</script> [x](http://evil.test/a) **b** \x1b]0;pwn\x07\x1b[31mred https://evil.test/z"


def test_markdown_and_text_neutralize_hostile_provider_text(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    reader.grades["1"] = (grade("1", HOSTILE, HOSTILE),)
    reader.announcements["1"] = (Announcement("1", "5", HOSTILE, HOSTILE * 50, "/d"),)
    reader.assignments["1"] = (Assignment("1", "1", HOSTILE, HOSTILE, HOSTILE),)
    for cmd in (
        ["grades", "overview"],
        ["announcements", "overview"],
        ["assignments", "dashboard"],
        ["student", "overview"],
    ):
        for fmt in ("text", "markdown"):
            code, out, _ = run(*cmd, "--format", fmt)
            assert code == 0
            assert "\x1b" not in out and "\x07" not in out
            assert "evil.test/z" not in out and "http://evil.test" not in out
            if fmt == "markdown":
                assert "<script>" not in out
                assert "](" not in out.replace("\\]\\(", "")


def test_long_text_is_bounded(wired: tuple[Courses, Reader]) -> None:
    _, reader = wired
    reader.grades["1"] = (grade("1", "L" * 5000),)
    out = run("grades", "overview")[1]
    assert "L" * 300 not in out


def test_renderers_are_pure_on_payload() -> None:
    service, _, reader = build([course("1")])
    snap = service.get_snapshot(provider_order=True)
    for fn in (
        grades_overview_json,
        assignments_dashboard_json,
        announcements_overview_json,
        student_overview_json,
    ):
        payload = fn(snap)
        assert render_view_text(payload).endswith("\n") and render_view_markdown(payload).endswith(
            "\n"
        )
    assert reader.calls  # reads happened only while building the snapshot


def test_course_resolution_error_is_valueerror() -> None:
    service, _, _ = build([course("1")])
    with pytest.raises(CourseResolutionError):
        service.get_snapshot(course_queries=("zzz",))


def test_provider_order_default_unchanged_for_snapshot() -> None:
    service, _, reader = build([course("1")])
    reader.grades["1"] = (grade("1", "B"), grade("1", "A"))
    assert [g.label for g in service.get_snapshot().courses[0].grade_items] == ["A", "B"]
    assert [g.label for g in service.get_snapshot(provider_order=True).courses[0].grade_items] == [
        "B",
        "A",
    ]
