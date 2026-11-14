from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime

import httpx
import pytest
import respx
from typer.testing import CliRunner

import d2l_cli.cli as cli_module
from d2l_cli.academic_overview_service import (
    AcademicCourseOverview,
    AcademicOverviewResult,
)
from d2l_cli.client.errors import (
    AuthExpiredError,
    MalformedResponseError,
    NetworkUnavailableError,
    NotAuthenticatedError,
    PermissionDeniedError,
    ProviderEndpointChangedError,
    UnexpectedProviderError,
)
from d2l_cli.models.announcement import (
    Announcement,
    AnnouncementCompleteness,
)
from d2l_cli.models.assignment import Assignment
from d2l_cli.models.assignment_completeness import (
    AssignmentCompleteness,
    AssignmentCompletenessStatus,
)
from d2l_cli.models.course import Course
from d2l_cli.models.grade import (
    FeedbackAvailability,
    GradeCompleteness,
    GradeItem,
)

runner = CliRunner()


class _Auth:
    def __init__(self, headers: Mapping[str, str] | Exception) -> None:
        self.headers = headers

    def header_provider(self) -> Mapping[str, str]:
        if isinstance(self.headers, Exception):
            raise self.headers
        return self.headers


class _OverviewService:
    def __init__(
        self,
        result: AcademicOverviewResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or AcademicOverviewResult(())
        self.error = error
        self.active_only_calls: list[bool] = []

    def get_overview(self, *, active_only: bool = True) -> AcademicOverviewResult:
        self.active_only_calls.append(active_only)
        if self.error is not None:
            raise self.error
        return self.result


def _course(course_id: str, *, active: bool = True) -> Course:
    return Course(
        id=course_id,
        code=f"BIO-{course_id}",
        name=f"Fictional Biology {course_id}",
        semester_name="Example Term",
        is_active=active,
        can_access=True,
        is_new_enrollment=False,
        start_date=None,
        end_date=None,
    )


def _assignment(number: int, course_id: str, *, dated: bool) -> Assignment:
    return Assignment(
        id=str(number),
        course_id=course_id,
        name=f"Activity {number}",
        submission_status=None,
        score=None,
        due_date=datetime(2027, 2, number, 14, 0) if dated else None,
    )


def _grade(label: str, course_id: str) -> GradeItem:
    return GradeItem(
        course_id=course_id,
        label=label,
        displayed_points="0",
        displayed_grade=None,
        displayed_weight="Shown weight",
        feedback_availability=FeedbackAvailability.NONE,
    )


def _announcement(
    number: int,
    course_id: str,
    *,
    title: str | None = None,
    body: str | None = None,
) -> Announcement:
    return Announcement(
        course_id=course_id,
        announcement_id=str(number),
        title=title or f"Fictional Update {number}",
        body=body,
        detail_reference=f"/d2l/le/news/{course_id}/{number}/view",
    )


def _entry(
    course: Course,
    *,
    assignments: tuple[Assignment, ...] = (),
    grades: tuple[GradeItem, ...] = (),
    announcements: tuple[Announcement, ...] = (),
    assignment_status: AssignmentCompletenessStatus = (
        AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY
    ),
) -> AcademicCourseOverview:
    return AcademicCourseOverview(
        course=course,
        assignments=assignments,
        assignment_completeness=AssignmentCompleteness(
            status=assignment_status,
            observed_row_count=len(assignments),
            page_size_ceiling=(
                None
                if assignment_status is AssignmentCompletenessStatus.UNKNOWN
                else (
                    len(assignments)
                    if assignment_status
                    is AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED
                    else len(assignments) + 1
                )
            ),
        ),
        grade_items=grades,
        grade_completeness=GradeCompleteness.UNKNOWN,
        announcements=announcements,
        announcement_completeness=AnnouncementCompleteness.UNKNOWN,
    )


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    *args: str,
    service: _OverviewService | None = None,
    auth_error: Exception | None = None,
):
    fake_service = service or _OverviewService()
    headers: Mapping[str, str] | Exception = (
        auth_error if auth_error is not None else {"Cookie": "fictional-session"}
    )
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: _Auth(headers))
    monkeypatch.setattr(
        cli_module,
        "_build_academic_overview_service",
        lambda config, provided_headers: fake_service,
    )
    return runner.invoke(cli_module.app, ["overview", *args]), fake_service


def test_overview_help_and_registration() -> None:
    root = runner.invoke(cli_module.app, ["--help"])
    command = runner.invoke(cli_module.app, ["overview", "--help"])

    assert root.exit_code == 0
    assert "overview" in root.stdout
    assert command.exit_code == 0
    assert "--all-courses" in command.stdout
    assert "--details" in command.stdout
    assert "--json" in command.stdout


def test_text_output_is_grouped_compact_and_uses_retrieved_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = _course("101")
    assignments = tuple(
        _assignment(number, course.id, dated=number in {1, 3}) for number in range(1, 5)
    )
    announcements = tuple(
        _announcement(number, course.id, body=f"Body {number}.")
        for number in range(1, 5)
    )
    service = _OverviewService(
        AcademicOverviewResult(
            (
                _entry(
                    course,
                    assignments=assignments,
                    grades=(_grade("Visible Lab", course.id),),
                    announcements=announcements,
                ),
            )
        )
    )

    result, observed = _invoke(monkeypatch, service=service)

    assert result.exit_code == 0
    assert observed.active_only_calls == [True]
    assert "Course: BIO-101 - Fictional Biology 101" in result.stdout
    assert "Retrieved assignments: 4" in result.stdout
    assert "Recognized due dates: 2" in result.stdout
    assert "Without recognized due dates: 2" in result.stdout
    assert "Visible grade items: 1" in result.stdout
    assert "Retrieved announcements: 4" in result.stdout
    assert "Activity 1\t2027-02-01T14:00:00" in result.stdout
    assert "Activity 2\t-" in result.stdout
    assert "Activity 3" in result.stdout
    assert "Activity 4" not in result.stdout
    assert "Fictional Update 1" in result.stdout
    assert "Fictional Update 2" in result.stdout
    assert "Fictional Update 3" in result.stdout
    assert "Fictional Update 4" not in result.stdout
    assert "Body 1" not in result.stdout
    assert "Use --details" in result.stdout
    assert "Visible Lab" not in result.stdout
    assert result.stderr.count("grade-list completeness") == 1
    assert result.stderr.count("announcement-list completeness") == 1
    for unsupported in ("overdue", "due soon", "gpa", "average", "missing work"):
        assert unsupported not in result.stdout.lower()


def test_details_show_all_assignments_and_exact_grade_display_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = _course("101")
    assignments = tuple(_assignment(number, course.id, dated=False) for number in range(1, 5))
    announcements = (
        _announcement(
            1,
            course.id,
            title="Line one\nLine\ttwo \x1b[31mred\x1b[0m",
            body="First paragraph.\n\n- First item\n- Second \x1b]8;;https://invalid\x07item\x1b]8;;\x07",
        ),
        _announcement(2, course.id, body=None),
    )
    service = _OverviewService(
        AcademicOverviewResult(
            (
                _entry(
                    course,
                    assignments=assignments,
                    grades=(_grade("Visible Lab", course.id),),
                    announcements=announcements,
                ),
            )
        )
    )

    result, _service = _invoke(monkeypatch, "--details", service=service)

    assert result.exit_code == 0
    assert "Activity 4\t-" in result.stdout
    assert "Grade item\tPoints\tGrade\tWeight\tFeedback" in result.stdout
    assert "Visible Lab\t0\t-\tShown weight\tnone" in result.stdout
    assert "1. Line one Line two red" in result.stdout
    assert "First paragraph." in result.stdout
    assert "- First item" in result.stdout
    assert "- Second item" in result.stdout
    assert "2. Fictional Update 2" in result.stdout
    assert "(empty body)" in result.stdout
    assert "\x1b" not in result.stdout
    assert "https://invalid" not in result.stdout
    assert "Use --details" not in result.stdout
    assert "percentage" not in result.stdout.lower()


def test_overview_text_output_strips_controls_from_all_academic_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = Course(
        id="101",
        code="BIO\x1b[31m-101\x1b[0m",
        name="Fictional\u202e Biology",
        semester_name="Example Term",
        is_active=True,
        can_access=True,
        is_new_enrollment=False,
        start_date=None,
        end_date=None,
    )
    assignment = Assignment(
        id="1",
        course_id=course.id,
        name="Activity\x1b]0;FICTIONAL\x07 One",
        submission_status=None,
        score=None,
    )
    grade = GradeItem(
        course_id=course.id,
        label="Visible\x1b[2J Lab",
        displayed_points="7\x9b31m / 10",
        displayed_grade="Pass\u202e",
        displayed_weight="10%",
        feedback_availability=FeedbackAvailability.NONE,
    )
    service = _OverviewService(
        AcademicOverviewResult(
            (_entry(course, assignments=(assignment,), grades=(grade,)),)
        )
    )

    result, _service = _invoke(monkeypatch, "--details", service=service)

    assert result.exit_code == 0
    assert "Course: BIO-101 - Fictional Biology" in result.stdout
    assert "Activity One\t-" in result.stdout
    assert "Visible Lab\t7 / 10\tPass\t10%\tnone" in result.stdout
    assert "\x1b" not in result.stdout
    assert "\x9b" not in result.stdout
    assert "\u202e" not in result.stdout


def test_json_groups_full_data_and_completeness_in_stable_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _course("101")
    second = _course("202", active=False)
    service = _OverviewService(
        AcademicOverviewResult(
            (
                _entry(
                    first,
                    assignments=(_assignment(1, first.id, dated=True),),
                    grades=(_grade("Visible Alpha", first.id),),
                    announcements=(
                        _announcement(2, first.id, body="Second body."),
                        _announcement(1, first.id, body=None),
                    ),
                ),
                _entry(
                    second,
                    assignment_status=AssignmentCompletenessStatus.UNKNOWN,
                ),
            )
        )
    )

    result, observed = _invoke(monkeypatch, "--json", "--all-courses", service=service)

    assert result.exit_code == 0
    assert observed.active_only_calls == [False]
    payload = json.loads(result.stdout)
    assert [entry["course"]["id"] for entry in payload["courses"]] == ["101", "202"]
    first_json = payload["courses"][0]
    assert list(first_json) == [
        "course",
        "retrieved_assignment_count",
        "recognized_due_date_count",
        "without_recognized_due_date_count",
        "visible_grade_item_count",
        "assignment_completeness",
        "grade_completeness",
        "assignments",
        "grade_items",
        "retrieved_announcement_count",
        "announcement_completeness",
        "announcements",
    ]
    assert first_json["retrieved_assignment_count"] == 1
    assert first_json["recognized_due_date_count"] == 1
    assert first_json["without_recognized_due_date_count"] == 0
    assert first_json["visible_grade_item_count"] == 1
    assert first_json["retrieved_announcement_count"] == 2
    assert first_json["assignments"][0]["due_date"] == "2027-02-01T14:00:00"
    assert first_json["grade_items"][0]["displayed_points"] == "0"
    assert first_json["grade_items"][0]["displayed_grade"] is None
    assert first_json["assignment_completeness"] == {
        "status": "below_page_size_boundary",
        "observed_row_count": 1,
        "page_size_ceiling": 2,
    }
    assert first_json["grade_completeness"] == "unknown"
    assert first_json["announcement_completeness"] == "unknown"
    assert [
        item["announcement_id"] for item in first_json["announcements"]
    ] == ["2", "1"]
    assert first_json["announcements"][0] == {
        "course_id": "101",
        "announcement_id": "2",
        "title": "Fictional Update 2",
        "body": "Second body.",
        "detail_reference": "/d2l/le/news/101/2/view",
    }
    assert payload["courses"][1]["assignment_completeness"]["status"] == "unknown"
    assert payload["courses"][1]["assignments"] == []
    assert payload["courses"][1]["grade_items"] == []
    assert payload["courses"][1]["announcements"] == []
    assert payload["courses"][1]["retrieved_announcement_count"] == 0
    assert "overall_grade" not in result.stdout
    assert "gpa" not in result.stdout.lower()
    assert "grade-list completeness" in result.stderr
    assert "assignment-list completeness" in result.stderr


def test_empty_result_and_empty_course_are_graceful(monkeypatch: pytest.MonkeyPatch) -> None:
    empty, _service = _invoke(monkeypatch, service=_OverviewService())
    course = _course("101")
    no_items, _service = _invoke(
        monkeypatch,
        service=_OverviewService(AcademicOverviewResult((_entry(course),))),
    )

    assert empty.exit_code == 0
    assert empty.stdout.strip() == "No active accessible courses found."
    assert empty.stderr == ""
    assert no_items.exit_code == 0
    assert "Retrieved assignments: 0" in no_items.stdout
    assert "Visible grade items: 0" in no_items.stdout
    assert "Retrieved announcements: 0" in no_items.stdout
    assert "Assignments: none retrieved." in no_items.stdout
    assert "Announcements: none retrieved." in no_items.stdout


def test_completeness_warnings_are_aggregated_without_course_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _course("PRIVATE101")
    second = _course("PRIVATE202")
    service = _OverviewService(
        AcademicOverviewResult(
            (
                _entry(
                    first,
                    assignment_status=AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED,
                ),
                _entry(
                    second,
                    assignment_status=AssignmentCompletenessStatus.UNKNOWN,
                ),
            )
        )
    )

    result, _service = _invoke(monkeypatch, "--json", service=service)

    assert result.exit_code == 0
    assert result.stderr.count("assignment-list completeness") == 1
    assert result.stderr.count("grade-list completeness") == 1
    assert result.stderr.count("announcement-list completeness") == 1
    assert "2 course(s)" in result.stderr
    assert "PRIVATE" not in result.stderr


def test_authentication_and_provider_failures_keep_stdout_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, _service = _invoke(
        monkeypatch,
        "--json",
        auth_error=NotAuthenticatedError("no stored session"),
    )
    provider, _service = _invoke(
        monkeypatch,
        "--json",
        service=_OverviewService(error=NetworkUnavailableError("provider unavailable")),
    )

    assert auth.exit_code == 1
    assert auth.stdout == ""
    assert "no stored session" in auth.stderr
    assert provider.exit_code == 1
    assert provider.stdout == ""
    assert "provider unavailable" in provider.stderr
    assert "completeness" not in auth.stderr
    assert "completeness" not in provider.stderr


@pytest.mark.parametrize(
    "error",
    [
        AuthExpiredError("stored Brightspace session expired"),
        PermissionDeniedError("provider denied access to this resource"),
        MalformedResponseError("provider response had an unexpected content type"),
        ProviderEndpointChangedError(
            "Announcements page structure is unsupported "
            "(ANNOUNCEMENTS_ITEM_ROW_GEOMETRY)"
        ),
        UnexpectedProviderError("provider returned an unexpected redirect"),
    ],
)
def test_overview_provider_failures_never_become_partial_success(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    result, _service = _invoke(
        monkeypatch,
        "--json",
        service=_OverviewService(error=error),
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    assert "completeness" not in result.stderr
    assert "PRIVATE" not in result.stderr


def test_course_mismatch_error_does_not_leak_private_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = ProviderEndpointChangedError(
        "academic overview received course-mismatched announcement data"
    )
    result, _service = _invoke(
        monkeypatch,
        "--json",
        service=_OverviewService(error=error),
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "course-mismatched announcement data" in result.stderr
    for secret in (
        "course-id-SECRET",
        "assignment-SECRET",
        "grade-SECRET",
        "announcement-SECRET",
    ):
        assert secret not in result.stderr


@respx.mock
def test_overview_uses_one_course_read_and_one_existing_read_per_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_build_auth_service",
        lambda config: _Auth({"Cookie": "fictional-session"}),
    )
    base_url = "https://kennesaw.view.usg.edu"
    course_id = "620000"
    course_route = respx.get(f"{base_url}/d2l/le/manageCourses/api/mycourses").mock(
        return_value=httpx.Response(
            200,
            json={
                "Courses": [
                    {
                        "OrgUnitId": course_id,
                        "Name": "Fictional Integrated Course",
                        "Code": "INT-1000",
                        "SemesterName": "Example Term",
                        "IsActive": True,
                        "CanAccessCourse": True,
                        "IsNewEnrollment": False,
                        "StartDate": None,
                        "EndDate": None,
                    }
                ],
                "Bookmark": None,
            },
        )
    )
    assignment_route = respx.get(
        f"{base_url}/d2l/lms/dropbox/user/folders_list.d2l?ou={course_id}"
    ).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=UTF-8"},
            text=f"""
            <d2l-table-wrapper><table>
              <tr><th>Assignment</th><th>Status</th><th>Score</th><th></th></tr>
              <tr><td><a href="/submit?db=1&amp;ou={course_id}">Example Task</a></td>
                  <td></td><td></td><td></td></tr>
            </table></d2l-table-wrapper>
            """,
        )
    )
    grade_index = respx.get(
        f"{base_url}/d2l/lms/grades/index.d2l?ou={course_id}"
    ).mock(
        return_value=httpx.Response(
            302,
            headers={
                "location": f"/d2l/lms/grades/my_grades/main.d2l?ou={course_id}"
            },
        )
    )
    grade_page = respx.get(
        f"{base_url}/d2l/lms/grades/my_grades/main.d2l?ou={course_id}"
    ).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=UTF-8"},
            text="""
            <d2l-table-wrapper><table>
              <tr><th>Grade Item</th><th>Points</th><th>Grade</th><th>Feedback</th></tr>
              <tr><th>Example Grade Item</th><td></td><td></td><td></td></tr>
            </table></d2l-table-wrapper>
            """,
        )
    )
    announcement_page = respx.get(
        f"{base_url}/d2l/lms/news/main.d2l?ou={course_id}"
    ).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=UTF-8"},
            text=f"""
            <form method="post" action="main.d2l?ou={course_id}"><table>
              <tr><th><a href="/d2l/le/news/{course_id}/501/view?ou={course_id}">
                Example Update</a></th><td>Unsupported date</td></tr>
              <tr><td colspan="2"><d2l-html-block
                html="&lt;p&gt;Example announcement body.&lt;/p&gt;">
              </d2l-html-block></td></tr>
            </table></form>
            """,
        )
    )

    result = runner.invoke(cli_module.app, ["overview", "--json"])

    assert result.exit_code == 0
    assert len(json.loads(result.stdout)["courses"]) == 1
    assert json.loads(result.stdout)["courses"][0]["retrieved_announcement_count"] == 1
    assert course_route.call_count == 1
    assert assignment_route.call_count == 1
    assert grade_index.call_count == 1
    assert grade_page.call_count == 1
    assert announcement_page.call_count == 1
    assert len(respx.calls) == 5
    assert {call.request.method for call in respx.calls} == {"GET"}
    assert all("/view" not in str(call.request.url) for call in respx.calls)
