from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pytest
from typer.testing import CliRunner

import d2l_cli.cli as cli_module
from d2l_cli.assignment_overview_service import (
    AssignmentOverviewItem,
    AssignmentOverviewResult,
    CourseAssignmentCompleteness,
)
from d2l_cli.auth_service import AuthState, AuthStatusResult
from d2l_cli.cli import app
from d2l_cli.client.errors import (
    NotAuthenticatedError,
    ProviderEndpointChangedError,
    SSOLoginFailedError,
    UnsupportedPaginationStateError,
)
from d2l_cli.models.assignment import Assignment
from d2l_cli.models.assignment_completeness import (
    AssignmentCompleteness,
    AssignmentCompletenessStatus,
    AssignmentListPage,
)
from d2l_cli.models.course import Course

runner = CliRunner()

_COURSE = Course(
    id="1",
    code="CS-1301",
    name="Intro to Programming",
    semester_name="Fall 2026",
    is_active=True,
    can_access=True,
    is_new_enrollment=False,
    start_date="2026-08-15T00:00:00.000Z",
    end_date="2026-12-15T00:00:00.000Z",
)


_DEFAULT_NOT_AUTHENTICATED = NotAuthenticatedError("not authenticated")


class FakeAuthService:
    def __init__(
        self,
        *,
        status_result: AuthStatusResult | None = None,
        login_error: Exception | None = None,
        headers: Mapping[str, str] | None | Exception = _DEFAULT_NOT_AUTHENTICATED,
    ) -> None:
        self._status_result = status_result
        self._login_error = login_error
        self._headers = headers

    def status(self) -> AuthStatusResult:
        assert self._status_result is not None
        return self._status_result

    def login(self, base_url: str) -> None:
        if self._login_error is not None:
            raise self._login_error

    def header_provider(self) -> Mapping[str, str]:
        if isinstance(self._headers, Exception):
            raise self._headers
        assert self._headers is not None
        return self._headers


class FakeCourseService:
    def __init__(self, courses: list[Course] | None = None, error: Exception | None = None) -> None:
        self._courses = courses if courses is not None else []
        self._error = error

    def list_courses(self) -> list[Course]:
        if self._error is not None:
            raise self._error
        return self._courses


_ASSIGNMENT = Assignment(
    id="90210",
    course_id="620000",
    name="Homework 1",
    submission_status="Submitted",
    score="18 / 20",
)


class FakeAssignmentService:
    def __init__(
        self,
        assignments: list[Assignment] | None = None,
        error: Exception | None = None,
        completeness: AssignmentCompleteness | None = None,
    ) -> None:
        self._assignments = assignments if assignments is not None else []
        self._error = error
        self._completeness = completeness or AssignmentCompleteness(
            status=AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY,
            observed_row_count=len(self._assignments),
            page_size_ceiling=len(self._assignments) + 1,
        )
        self.requested_course_ids: list[str] = []

    def list_assignments(self, course_id: str) -> list[Assignment]:
        self.requested_course_ids.append(course_id)
        if self._error is not None:
            raise self._error
        return self._assignments

    def get_assignment_list(self, course_id: str) -> AssignmentListPage:
        self.requested_course_ids.append(course_id)
        if self._error is not None:
            raise self._error
        return AssignmentListPage(
            assignments=tuple(self._assignments),
            completeness=self._completeness,
        )


class FakeAssignmentOverviewService:
    def __init__(
        self,
        items: list[AssignmentOverviewItem] | None = None,
        error: Exception | None = None,
        course_completeness: list[CourseAssignmentCompleteness] | None = None,
    ) -> None:
        self._items = items if items is not None else []
        self._error = error
        if course_completeness is None:
            seen: dict[str, Course] = {}
            for item in self._items:
                seen.setdefault(item.course.id, item.course)
            course_completeness = [
                CourseAssignmentCompleteness(
                    course=course,
                    completeness=AssignmentCompleteness(
                        status=AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY,
                        observed_row_count=sum(
                            item.course.id == course.id for item in self._items
                        ),
                        page_size_ceiling=(
                            sum(item.course.id == course.id for item in self._items) + 1
                        ),
                    ),
                )
                for course in seen.values()
            ]
        self._course_completeness = course_completeness
        self.calls: list[tuple[bool, bool]] = []

    def list_overview(
        self, *, active_only: bool = True, chronological: bool = False
    ) -> list[AssignmentOverviewItem]:
        self.calls.append((active_only, chronological))
        if self._error is not None:
            raise self._error
        return self._items

    def get_overview(
        self, *, active_only: bool = True, chronological: bool = False
    ) -> AssignmentOverviewResult:
        self.calls.append((active_only, chronological))
        if self._error is not None:
            raise self._error
        return AssignmentOverviewResult(
            items=tuple(self._items),
            course_completeness=tuple(self._course_completeness),
        )


def test_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Brightspace" in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.output


@pytest.mark.parametrize(
    ("state", "expected_exit_code"),
    [
        (AuthState.AUTHENTICATED, 0),
        (AuthState.NOT_AUTHENTICATED, 1),
        (AuthState.STORED_SESSION_UNREADABLE, 1),
        (AuthState.SESSION_EXPIRED, 1),
        (AuthState.STORAGE_UNAVAILABLE, 1),
        (AuthState.PROVIDER_UNAVAILABLE, 1),
    ],
)
def test_auth_status_exit_codes(
    monkeypatch: pytest.MonkeyPatch, state: AuthState, expected_exit_code: int
) -> None:
    fake = FakeAuthService(status_result=AuthStatusResult(state, "detail message"))
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == expected_exit_code
    assert state.value in result.stdout
    assert "detail message" in result.stdout


def test_auth_login_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAuthService()
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake)

    result = runner.invoke(app, ["auth", "login"])

    assert result.exit_code == 0
    assert "successful" in result.stdout.lower()


def test_auth_login_sso_failure_reports_error_on_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAuthService(login_error=SSOLoginFailedError("timed out"))
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake)

    result = runner.invoke(app, ["auth", "login"])

    assert result.exit_code == 1
    assert "timed out" in result.stderr
    assert result.stdout == ""


def test_courses_list_not_authenticated_human_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAuthService(headers=NotAuthenticatedError("no stored session"))
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake)

    result = runner.invoke(app, ["courses", "list"])

    assert result.exit_code == 1
    assert "no stored session" in result.stderr


def test_courses_list_not_authenticated_json_mode_stdout_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAuthService(headers=NotAuthenticatedError("no stored session"))
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake)

    result = runner.invoke(app, ["courses", "list", "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "no stored session" in result.stderr


def test_courses_list_human_output(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module, "_build_course_service", lambda config, headers: FakeCourseService([_COURSE])
    )

    result = runner.invoke(app, ["courses", "list"])

    assert result.exit_code == 0
    assert "CS-1301" in result.stdout
    assert "Intro to Programming" in result.stdout


def test_courses_text_output_strips_terminal_controls_without_changing_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile = Course(
        id="1",
        code="\x1b[31mCS-1301\x1b[0m",
        name="Intro\x1b]0;FICTIONAL_TITLE\x07 Programming",
        semester_name="Fall\u202e 2026",
        is_active=True,
        can_access=True,
        is_new_enrollment=False,
        start_date=None,
        end_date=None,
    )
    fake_auth = FakeAuthService(headers={"Cookie": "fictional-session"})
    service = FakeCourseService([hostile])
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(cli_module, "_build_course_service", lambda config, headers: service)

    text_result = runner.invoke(app, ["courses", "list"])
    json_result = runner.invoke(app, ["courses", "list", "--json"])

    assert text_result.exit_code == 0
    assert text_result.stdout.strip() == "CS-1301\tIntro Programming\tFall 2026"
    assert "\x1b" not in text_result.stdout
    assert "\u202e" not in text_result.stdout
    assert json.loads(json_result.stdout)["courses"][0]["code"] == hostile.code
    assert json.loads(json_result.stdout)["courses"][0]["name"] == hostile.name


def test_courses_list_human_output_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module, "_build_course_service", lambda config, headers: FakeCourseService([])
    )

    result = runner.invoke(app, ["courses", "list"])

    assert result.exit_code == 0
    assert "No courses found" in result.stdout


def test_courses_list_json_output_is_pure_json_on_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module, "_build_course_service", lambda config, headers: FakeCourseService([_COURSE])
    )

    result = runner.invoke(app, ["courses", "list", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "courses": [
            {
                "id": "1",
                "code": "CS-1301",
                "name": "Intro to Programming",
                "semester_name": "Fall 2026",
                "is_active": True,
                "can_access": True,
                "is_new_enrollment": False,
                "start_date": "2026-08-15T00:00:00.000Z",
                "end_date": "2026-12-15T00:00:00.000Z",
            }
        ]
    }


def test_courses_list_json_keys_are_stable_and_documented(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module, "_build_course_service", lambda config, headers: FakeCourseService([_COURSE])
    )

    result = runner.invoke(app, ["courses", "list", "--json"])
    payload = json.loads(result.stdout)
    course_keys = set(payload["courses"][0].keys())
    assert course_keys == {
        "id",
        "code",
        "name",
        "semester_name",
        "is_active",
        "can_access",
        "is_new_enrollment",
        "start_date",
        "end_date",
    }


def test_courses_list_provider_error_reports_on_stderr_not_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module,
        "_build_course_service",
        lambda config, headers: FakeCourseService(
            error=UnsupportedPaginationStateError("more pages exist")
        ),
    )

    result = runner.invoke(app, ["courses", "list", "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "more pages exist" in result.stderr


# --- assignments list -------------------------------------------------------


def test_assignments_list_requires_course_option() -> None:
    result = runner.invoke(app, ["assignments", "list"])
    assert result.exit_code != 0
    assert "--course" in result.stderr or "--course" in result.stdout


def test_assignments_list_not_authenticated_human_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAuthService(headers=NotAuthenticatedError("no stored session"))
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake)

    result = runner.invoke(app, ["assignments", "list", "--course", "620000"])

    assert result.exit_code == 1
    assert "no stored session" in result.stderr


def test_assignments_list_not_authenticated_json_mode_stdout_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAuthService(headers=NotAuthenticatedError("no stored session"))
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake)

    result = runner.invoke(app, ["assignments", "list", "--course", "620000", "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "no stored session" in result.stderr


def test_assignments_list_human_output(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module,
        "_build_assignment_service",
        lambda config, headers: FakeAssignmentService([_ASSIGNMENT]),
    )

    result = runner.invoke(app, ["assignments", "list", "--course", "620000"])

    assert result.exit_code == 0
    assert "Homework 1" in result.stdout
    assert "Submitted" in result.stdout
    assert "18 / 20" in result.stdout


def test_assignments_text_output_strips_terminal_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile = Assignment(
        id="90210",
        course_id="620000",
        name="Homework\x1b[2J One",
        submission_status="Sub\x1b]8;;https://external.invalid\x07mitted\x1b]8;;\x07",
        score="18\u202e / 20",
    )
    fake_auth = FakeAuthService(headers={"Cookie": "fictional-session"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module,
        "_build_assignment_service",
        lambda config, headers: FakeAssignmentService([hostile]),
    )

    result = runner.invoke(app, ["assignments", "list", "--course", "620000"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "Homework One\tSubmitted\t18 / 20\t-"
    assert "\x1b" not in result.stdout
    assert "\u202e" not in result.stdout


def test_assignments_list_human_output_does_not_show_unsupported_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module,
        "_build_assignment_service",
        lambda config, headers: FakeAssignmentService([_ASSIGNMENT]),
    )

    result = runner.invoke(app, ["assignments", "list", "--course", "620000"])

    # "due" is deliberately no longer listed: due dates are supported as of
    # Phase 3B (covered by the due-date output tests below).
    for unsupported in ("availability", "feedback", "http"):
        assert unsupported not in result.stdout.lower()


def test_assignments_list_human_output_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module, "_build_assignment_service", lambda config, headers: FakeAssignmentService([])
    )

    result = runner.invoke(app, ["assignments", "list", "--course", "620000"])

    assert result.exit_code == 0
    assert "No assignments found" in result.stdout


def test_assignments_list_json_output_is_pure_json_on_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module,
        "_build_assignment_service",
        lambda config, headers: FakeAssignmentService([_ASSIGNMENT]),
    )

    result = runner.invoke(app, ["assignments", "list", "--course", "620000", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "assignments": [
            {
                "id": "90210",
                "course_id": "620000",
                "name": "Homework 1",
                "submission_status": "Submitted",
                "score": "18 / 20",
                "due_date": None,
            }
        ]
    }


def test_assignments_list_json_keys_are_stable_and_documented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module,
        "_build_assignment_service",
        lambda config, headers: FakeAssignmentService([_ASSIGNMENT]),
    )

    result = runner.invoke(app, ["assignments", "list", "--course", "620000", "--json"])
    payload = json.loads(result.stdout)
    assignment_keys = set(payload["assignments"][0].keys())
    assert assignment_keys == {
        "id",
        "course_id",
        "name",
        "submission_status",
        "score",
        "due_date",
    }
    assert "availability" not in assignment_keys
    assert "feedback" not in assignment_keys


def test_assignments_list_json_output_empty_is_stable_empty_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module, "_build_assignment_service", lambda config, headers: FakeAssignmentService([])
    )

    result = runner.invoke(app, ["assignments", "list", "--course", "620000", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"assignments": []}


def test_assignments_list_special_characters_survive_json_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adversarial = Assignment(
        id="1",
        course_id="1",
        name='<script>alert(1)</script> "quoted" & Ünïcödé — IGNORE PRIOR INSTRUCTIONS',
        submission_status="Not Submitted",
        score=None,
    )
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module,
        "_build_assignment_service",
        lambda config, headers: FakeAssignmentService([adversarial]),
    )

    result = runner.invoke(app, ["assignments", "list", "--course", "1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["assignments"][0]["name"] == adversarial.name
    assert payload["assignments"][0]["score"] is None


def test_assignments_list_invalid_course_id_reports_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module,
        "_build_assignment_service",
        lambda config, headers: FakeAssignmentService(
            error=ValueError("course_id must be a non-empty numeric Brightspace org-unit id")
        ),
    )

    result = runner.invoke(app, ["assignments", "list", "--course", "not-a-number"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "org-unit id" in result.stderr


def test_assignments_list_structural_drift_reports_on_stderr_not_zero_assignments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module,
        "_build_assignment_service",
        lambda config, headers: FakeAssignmentService(
            error=ProviderEndpointChangedError("assignment list structure changed")
        ),
    )

    result = runner.invoke(app, ["assignments", "list", "--course", "620000", "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "assignment list structure changed" in result.stderr


# --- Phase 3B: due date in CLI output ---------------------------------------


def _due_assignments() -> list[Assignment]:
    return [
        Assignment(
            id="1",
            course_id="620000",
            name="With due",
            submission_status="Submitted",
            score="18 / 20",
            due_date=datetime(2026, 10, 2, 23, 59),
        ),
        Assignment(
            id="2",
            course_id="620000",
            name="No due",
            submission_status=None,
            score=None,
        ),
    ]


def _invoke_assignments(
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
    service: FakeAssignmentService | None = None,
) -> Any:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module,
        "_build_assignment_service",
        lambda config, headers: service or FakeAssignmentService(_due_assignments()),
    )
    return runner.invoke(app, ["assignments", "list", "--course", "620000", *extra])


def test_assignments_list_text_output_appends_due_column_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _invoke_assignments(monkeypatch)

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    # name, status, score keep their positions; due is appended; "-" when absent.
    assert lines == [
        "With due\tSubmitted\t18 / 20\t2026-10-02T23:59:00",
        "No due\t-\t-\t-",
    ]


def test_assignments_list_json_due_date_is_naive_iso_or_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _invoke_assignments(monkeypatch, "--json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [a["due_date"] for a in payload["assignments"]] == ["2026-10-02T23:59:00", None]
    # No timezone claim in the serialization, and no availability fields.
    assert "+" not in payload["assignments"][0]["due_date"]
    assert not payload["assignments"][0]["due_date"].endswith("Z")
    for a in payload["assignments"]:
        assert not {k for k in a if "avail" in k or k in ("start_date", "end_date")}


def test_assignments_list_warns_without_changing_text_columns_at_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completeness = AssignmentCompleteness(
        status=AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED,
        observed_row_count=2,
        page_size_ceiling=2,
    )
    result = _invoke_assignments(
        monkeypatch,
        service=FakeAssignmentService(_due_assignments(), completeness=completeness),
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "With due\tSubmitted\t18 / 20\t2026-10-02T23:59:00",
        "No due\t-\t-\t-",
    ]
    assert "may be truncated" in result.stderr
    assert "GET-only pagination is unavailable" in result.stderr


def test_assignments_list_json_completeness_is_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completeness = AssignmentCompleteness(
        status=AssignmentCompletenessStatus.UNKNOWN,
        observed_row_count=2,
        page_size_ceiling=None,
    )
    service = FakeAssignmentService(_due_assignments(), completeness=completeness)

    default_result = _invoke_assignments(monkeypatch, "--json", service=service)
    explicit_result = _invoke_assignments(
        monkeypatch,
        "--json",
        "--include-completeness",
        service=service,
    )

    assert set(json.loads(default_result.stdout)) == {"assignments"}
    assert json.loads(explicit_result.stdout)["completeness"] == {
        "status": "unknown",
        "observed_row_count": 2,
        "page_size_ceiling": None,
    }
    assert "completeness for this course is unknown" in explicit_result.stderr


# --- cross-course assignment overview --------------------------------------


_OVERVIEW_COURSE = Course(
    id="620000",
    code="CS-1301",
    name="Intro to Programming",
    semester_name="Fall 2026",
    is_active=True,
    can_access=True,
    is_new_enrollment=False,
    start_date=None,
    end_date=None,
)


def _overview_items() -> list[AssignmentOverviewItem]:
    dated, undated = _due_assignments()
    return [
        AssignmentOverviewItem(course=_OVERVIEW_COURSE, assignment=dated),
        AssignmentOverviewItem(course=_OVERVIEW_COURSE, assignment=undated),
    ]


def _invoke_overview(
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
    service: FakeAssignmentOverviewService | None = None,
) -> tuple[Any, FakeAssignmentOverviewService]:
    fake_auth = FakeAuthService(headers={"Cookie": "d2lSessionVal=abc"})
    fake_service = service or FakeAssignmentOverviewService(_overview_items())
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)
    monkeypatch.setattr(
        cli_module,
        "_build_assignment_overview_service",
        lambda config, headers: fake_service,
    )
    return runner.invoke(app, ["assignments", "overview", *extra]), fake_service


def test_assignments_overview_human_output_shows_course_assignment_and_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _service = _invoke_overview(monkeypatch)

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "CS-1301\tIntro to Programming\tWith due\t2026-10-02T23:59:00",
        "CS-1301\tIntro to Programming\tNo due\t-",
    ]


def test_assignments_overview_text_output_strips_terminal_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile_course = Course(
        id="620000",
        code="CS\x1b[31m-1301\x1b[0m",
        name="Intro\u202e Programming",
        semester_name="Example Term",
        is_active=True,
        can_access=True,
        is_new_enrollment=False,
        start_date=None,
        end_date=None,
    )
    hostile_assignment = Assignment(
        id="1",
        course_id=hostile_course.id,
        name="With\x1b]0;FICTIONAL\x07 due",
        submission_status=None,
        score=None,
    )
    service = FakeAssignmentOverviewService(
        [AssignmentOverviewItem(course=hostile_course, assignment=hostile_assignment)]
    )

    result, _ = _invoke_overview(monkeypatch, service=service)

    assert result.exit_code == 0
    assert result.stdout.strip() == "CS-1301\tIntro Programming\tWith due\t-"
    assert "\x1b" not in result.stdout
    assert "\u202e" not in result.stdout


def test_assignments_overview_json_contract_and_missing_due_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _service = _invoke_overview(monkeypatch, "--json")

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "assignments": [
            {
                "id": "1",
                "course_id": "620000",
                "course_code": "CS-1301",
                "course_name": "Intro to Programming",
                "name": "With due",
                "due_date": "2026-10-02T23:59:00",
            },
            {
                "id": "2",
                "course_id": "620000",
                "course_code": "CS-1301",
                "course_name": "Intro to Programming",
                "name": "No due",
                "due_date": None,
            },
        ]
    }


def test_assignments_overview_options_reach_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, service = _invoke_overview(monkeypatch, "--chronological", "--all-courses")

    assert result.exit_code == 0
    assert service.calls == [(False, True)]


def test_assignments_overview_defaults_to_active_courses_and_provider_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, service = _invoke_overview(monkeypatch)

    assert result.exit_code == 0
    assert service.calls == [(True, False)]


@pytest.mark.parametrize(
    ("extra", "expected"),
    [([], "active accessible courses"), (["--all-courses"], "accessible courses")],
)
def test_assignments_overview_empty_result_is_clear(
    monkeypatch: pytest.MonkeyPatch, extra: list[str], expected: str
) -> None:
    empty = FakeAssignmentOverviewService([])
    result, _service = _invoke_overview(monkeypatch, *extra, service=empty)

    assert result.exit_code == 0
    assert result.stdout.strip() == f"No assignments found across {expected}."


def test_assignments_overview_empty_json_is_stable_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _service = _invoke_overview(
        monkeypatch, "--json", service=FakeAssignmentOverviewService([])
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"assignments": []}


def test_assignments_overview_not_authenticated_keeps_json_stdout_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_auth = FakeAuthService(headers=NotAuthenticatedError("no stored session"))
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: fake_auth)

    result = runner.invoke(app, ["assignments", "overview", "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "no stored session" in result.stderr


def test_assignments_overview_provider_error_is_not_reported_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeAssignmentOverviewService(
        error=ProviderEndpointChangedError("assignment list structure changed")
    )
    result, _service = _invoke_overview(monkeypatch, "--json", service=service)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "assignment list structure changed" in result.stderr


def test_chronological_overview_aggregates_warning_without_academic_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_course = Course(
        id="730000",
        code="MATH-2202",
        name="Fictional Calculus",
        semester_name="Fall 2026",
        is_active=True,
        can_access=True,
        is_new_enrollment=False,
        start_date=None,
        end_date=None,
    )
    dated, _undated = _due_assignments()
    other_assignment = Assignment(
        id="3",
        course_id=other_course.id,
        name="Fictional Worksheet",
        submission_status=None,
        score=None,
        due_date=datetime(2026, 9, 1, 8, 0),
    )
    course_results = [
        CourseAssignmentCompleteness(
            course=_OVERVIEW_COURSE,
            completeness=AssignmentCompleteness(
                status=AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY,
                observed_row_count=1,
                page_size_ceiling=10,
            ),
        ),
        CourseAssignmentCompleteness(
            course=other_course,
            completeness=AssignmentCompleteness(
                status=AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED,
                observed_row_count=25,
                page_size_ceiling=25,
            ),
        ),
    ]
    service = FakeAssignmentOverviewService(
        [
            AssignmentOverviewItem(course=other_course, assignment=other_assignment),
            AssignmentOverviewItem(course=_OVERVIEW_COURSE, assignment=dated),
        ],
        course_completeness=course_results,
    )

    result, _ = _invoke_overview(
        monkeypatch, "--chronological", service=service
    )

    assert result.exit_code == 0
    assert "assignment-list completeness is uncertain for 1 course(s)" in result.stderr
    assert "1 reached the observed page-size boundary" in result.stderr
    for private_value in ("MATH-2202", "Fictional Calculus", "730000", "CS-1301"):
        assert private_value not in result.stderr
    assert all(len(line.split("\t")) == 4 for line in result.stdout.splitlines())


def test_overview_json_completeness_is_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completeness = CourseAssignmentCompleteness(
        course=_OVERVIEW_COURSE,
        completeness=AssignmentCompleteness(
            status=AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED,
            observed_row_count=2,
            page_size_ceiling=2,
        ),
    )
    service = FakeAssignmentOverviewService(
        _overview_items(), course_completeness=[completeness]
    )

    default_result, _ = _invoke_overview(monkeypatch, "--json", service=service)
    explicit_result, _ = _invoke_overview(
        monkeypatch,
        "--json",
        "--include-completeness",
        service=service,
    )

    assert set(json.loads(default_result.stdout)) == {"assignments"}
    payload = json.loads(explicit_result.stdout)
    assert payload["course_completeness"] == [
        {
            "course_id": "620000",
            "course_code": "CS-1301",
            "status": "potentially_truncated",
            "observed_row_count": 2,
            "page_size_ceiling": 2,
        }
    ]


def test_version_flag_reports_installed_version() -> None:
    from typer.testing import CliRunner

    from d2l_cli.cli import app

    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.startswith("d2l-cli ")
