from __future__ import annotations

from datetime import datetime

import pytest

from d2l_cli.assignment_overview_service import AssignmentOverviewService
from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.models.assignment import Assignment
from d2l_cli.models.assignment_completeness import (
    AssignmentCompleteness,
    AssignmentCompletenessStatus,
    AssignmentListPage,
)
from d2l_cli.models.course import Course


def _course(
    course_id: str, *, active: bool = True, accessible: bool = True, code: str | None = None
) -> Course:
    return Course(
        id=course_id,
        code=code or f"COURSE-{course_id}",
        name=f"Fictional Course {course_id}",
        semester_name="Fictional Term",
        is_active=active,
        can_access=accessible,
        is_new_enrollment=False,
        start_date=None,
        end_date=None,
    )


def _assignment(
    assignment_id: str, course_id: str, due: datetime | None = None
) -> Assignment:
    return Assignment(
        id=assignment_id,
        course_id=course_id,
        name=f"Fictional Assignment {assignment_id}",
        submission_status=None,
        score=None,
        due_date=due,
    )


class _Courses:
    def __init__(self, courses: list[Course]) -> None:
        self.courses = courses

    def list_courses(self) -> list[Course]:
        return self.courses


class _Assignments:
    def __init__(
        self,
        by_course: dict[str, list[Assignment]],
        statuses: dict[str, AssignmentCompletenessStatus] | None = None,
    ) -> None:
        self.by_course = by_course
        self.statuses = statuses or {}
        self.requested: list[str] = []

    def list_assignments(self, course_id: str) -> list[Assignment]:
        self.requested.append(course_id)
        return self.by_course.get(course_id, [])

    def get_assignment_list(self, course_id: str) -> AssignmentListPage:
        self.requested.append(course_id)
        assignments = tuple(self.by_course.get(course_id, []))
        status = self.statuses.get(
            course_id, AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY
        )
        ceiling = None if status == AssignmentCompletenessStatus.UNKNOWN else len(assignments) + 1
        if status == AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED:
            ceiling = len(assignments)
        return AssignmentListPage(
            assignments=assignments,
            completeness=AssignmentCompleteness(
                status=status,
                observed_row_count=len(assignments),
                page_size_ceiling=ceiling,
            ),
        )


def test_aggregates_multiple_courses_and_preserves_associations() -> None:
    courses = [_course("101"), _course("202")]
    assignments = _Assignments(
        {
            "101": [_assignment("1", "101")],
            "202": [_assignment("1", "202"), _assignment("2", "202")],
        }
    )

    items = AssignmentOverviewService(_Courses(courses), assignments).list_overview()

    assert [(item.course.id, item.assignment.id) for item in items] == [
        ("101", "1"),
        ("202", "1"),
        ("202", "2"),
    ]
    assert assignments.requested == ["101", "202"]


def test_skips_inaccessible_and_by_default_inactive_courses() -> None:
    courses = [
        _course("101"),
        _course("202", active=False),
        _course("303", accessible=False),
    ]
    assignments = _Assignments({course.id: [_assignment("1", course.id)] for course in courses})

    items = AssignmentOverviewService(_Courses(courses), assignments).list_overview()

    assert [item.course.id for item in items] == ["101"]
    assert assignments.requested == ["101"]


def test_all_courses_includes_inactive_but_never_inaccessible_courses() -> None:
    courses = [
        _course("101"),
        _course("202", active=False),
        _course("303", accessible=False),
    ]
    assignments = _Assignments({course.id: [_assignment("1", course.id)] for course in courses})

    items = AssignmentOverviewService(_Courses(courses), assignments).list_overview(
        active_only=False
    )

    assert [item.course.id for item in items] == ["101", "202"]
    assert assignments.requested == ["101", "202"]


def test_chronological_order_places_missing_dates_last_and_is_stable() -> None:
    course_one = _course("101")
    course_two = _course("202")
    late = _assignment("late", "101", datetime(2026, 11, 3, 9, 0))
    undated_one = _assignment("undated-1", "101")
    early_one = _assignment("early-1", "202", datetime(2026, 10, 1, 8, 0))
    early_two = _assignment("early-2", "101", datetime(2026, 10, 1, 8, 0))
    undated_two = _assignment("undated-2", "202")

    items = AssignmentOverviewService(
        _Courses([course_one, course_two]),
        _Assignments(
            {
                "101": [late, undated_one, early_two],
                "202": [early_one, undated_two],
            }
        ),
    ).list_overview(chronological=True)

    assert [item.assignment.id for item in items] == [
        "early-2",
        "early-1",
        "late",
        "undated-1",
        "undated-2",
    ]


def test_default_order_does_not_reorder_missing_or_parseable_dates() -> None:
    course = _course("101")
    original = [
        _assignment("undated", "101"),
        _assignment("later", "101", datetime(2026, 12, 1, 12, 0)),
        _assignment("earlier", "101", datetime(2026, 10, 1, 12, 0)),
    ]

    items = AssignmentOverviewService(
        _Courses([course]), _Assignments({"101": original})
    ).list_overview()

    assert [item.assignment.id for item in items] == ["undated", "later", "earlier"]


def test_malformed_course_association_is_rejected() -> None:
    service = AssignmentOverviewService(
        _Courses([_course("101")]),
        _Assignments({"101": [_assignment("1", "999")]}),
    )

    with pytest.raises(ProviderEndpointChangedError, match="course association"):
        service.list_overview()


def test_empty_courses_and_courses_without_assignments_return_empty() -> None:
    assert AssignmentOverviewService(_Courses([]), _Assignments({})).list_overview() == []
    assert AssignmentOverviewService(
        _Courses([_course("101")]), _Assignments({"101": []})
    ).list_overview() == []


def test_overview_reports_per_course_completeness_with_one_truncated_course() -> None:
    courses = [_course("101"), _course("202")]
    assignments = _Assignments(
        {
            "101": [_assignment("1", "101")],
            "202": [_assignment("2", "202")],
        },
        {"202": AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED},
    )

    result = AssignmentOverviewService(_Courses(courses), assignments).get_overview(
        chronological=True
    )

    assert [item.course.id for item in result.items] == ["101", "202"]
    assert [entry.course.id for entry in result.course_completeness] == ["101", "202"]
    assert [entry.completeness.status for entry in result.course_completeness] == [
        AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY,
        AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED,
    ]
