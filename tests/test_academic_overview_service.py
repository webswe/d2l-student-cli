from __future__ import annotations

import traceback
from datetime import datetime

import pytest

from d2l_cli.academic_overview_service import AcademicOverviewService
from d2l_cli.client.errors import NetworkUnavailableError, ProviderEndpointChangedError
from d2l_cli.models.announcement import Announcement, AnnouncementListPage
from d2l_cli.models.assignment import Assignment
from d2l_cli.models.assignment_completeness import (
    AssignmentCompleteness,
    AssignmentCompletenessStatus,
    AssignmentListPage,
)
from d2l_cli.models.course import Course
from d2l_cli.models.grade import (
    FeedbackAvailability,
    GradeCompleteness,
    GradeItem,
    GradeListPage,
)


def _course(course_id: str, *, active: bool = True, accessible: bool = True) -> Course:
    return Course(
        id=course_id,
        code=f"SUBJ-{course_id}",
        name=f"Fictional Course {course_id}",
        semester_name="Example Term",
        is_active=active,
        can_access=accessible,
        is_new_enrollment=False,
        start_date=None,
        end_date=None,
    )


def _assignment(
    assignment_id: str,
    course_id: str,
    *,
    due_date: datetime | None = None,
) -> Assignment:
    return Assignment(
        id=assignment_id,
        course_id=course_id,
        name=f"Fictional Task {assignment_id}",
        submission_status=None,
        score=None,
        due_date=due_date,
    )


def _grade(item_label: str, course_id: str) -> GradeItem:
    return GradeItem(
        course_id=course_id,
        label=item_label,
        displayed_points="Shown points",
        displayed_grade=None,
        displayed_weight=None,
        feedback_availability=FeedbackAvailability.UNKNOWN,
    )


def _announcement(announcement_id: str, course_id: str) -> Announcement:
    return Announcement(
        course_id=course_id,
        announcement_id=announcement_id,
        title=f"Fictional Update {announcement_id}",
        body=f"Fictional body {announcement_id}.",
        detail_reference=f"/d2l/le/news/{course_id}/{announcement_id}/view",
    )


class _Courses:
    def __init__(self, courses: list[Course], *, error: Exception | None = None) -> None:
        self.courses = courses
        self.error = error
        self.calls = 0

    def list_courses(self) -> list[Course]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.courses


class _Assignments:
    def __init__(
        self,
        by_course: dict[str, list[Assignment]],
        *,
        statuses: dict[str, AssignmentCompletenessStatus] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.by_course = by_course
        self.statuses = statuses or {}
        self.errors = errors or {}
        self.requested: list[str] = []

    def get_assignment_list(self, course_id: str) -> AssignmentListPage:
        self.requested.append(course_id)
        if course_id in self.errors:
            raise self.errors[course_id]
        assignments = tuple(self.by_course.get(course_id, []))
        status = self.statuses.get(
            course_id,
            AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY,
        )
        return AssignmentListPage(
            assignments=assignments,
            completeness=AssignmentCompleteness(
                status=status,
                observed_row_count=len(assignments),
                page_size_ceiling=(
                    None
                    if status is AssignmentCompletenessStatus.UNKNOWN
                    else len(assignments) + 1
                ),
            ),
        )


class _Grades:
    def __init__(
        self,
        by_course: dict[str, list[GradeItem]],
        *,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.by_course = by_course
        self.errors = errors or {}
        self.requested: list[str] = []

    def get_grade_list(self, course_id: str) -> GradeListPage:
        self.requested.append(course_id)
        if course_id in self.errors:
            raise self.errors[course_id]
        items = tuple(self.by_course.get(course_id, []))
        return GradeListPage(
            items=items,
            category_row_count=0,
            header_effective_width=4,
            has_weight_column=False,
        )


class _Announcements:
    def __init__(
        self,
        by_course: dict[str, list[Announcement]],
        *,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.by_course = by_course
        self.errors = errors or {}
        self.requested: list[str] = []

    def get_announcement_list(self, course_id: str) -> AnnouncementListPage:
        self.requested.append(course_id)
        if course_id in self.errors:
            raise self.errors[course_id]
        return AnnouncementListPage(tuple(self.by_course.get(course_id, [])))


def test_groups_multiple_courses_and_preserves_order_and_counts() -> None:
    courses = _Courses([_course("101"), _course("202")])
    assignments = _Assignments(
        {
            "101": [
                _assignment("a", "101", due_date=datetime(2027, 1, 5, 9, 30)),
                _assignment("b", "101"),
            ],
            "202": [_assignment("c", "202")],
        },
        statuses={"202": AssignmentCompletenessStatus.UNKNOWN},
    )
    grades = _Grades(
        {
            "101": [_grade("Visible One", "101")],
            "202": [_grade("Visible Two", "202"), _grade("Visible Three", "202")],
        }
    )
    announcements = _Announcements(
        {
            "101": [_announcement("n1", "101"), _announcement("n2", "101")],
            "202": [_announcement("n3", "202")],
        }
    )

    result = AcademicOverviewService(
        courses, assignments, grades, announcements
    ).get_overview()

    assert [entry.course.id for entry in result.courses] == ["101", "202"]
    assert [assignment.id for assignment in result.courses[0].assignments] == ["a", "b"]
    assert [item.label for item in result.courses[1].grade_items] == [
        "Visible Two",
        "Visible Three",
    ]
    assert [
        announcement.announcement_id
        for announcement in result.courses[0].announcements
    ] == ["n1", "n2"]
    assert result.courses[0].retrieved_assignment_count == 2
    assert result.courses[0].recognized_due_date_count == 1
    assert result.courses[0].without_recognized_due_date_count == 1
    assert result.courses[1].visible_grade_item_count == 2
    assert result.courses[0].retrieved_announcement_count == 2
    assert result.courses[1].assignment_completeness.status is (
        AssignmentCompletenessStatus.UNKNOWN
    )
    assert result.courses[1].grade_completeness is GradeCompleteness.UNKNOWN
    assert result.courses[1].announcement_completeness.value == "unknown"
    assert courses.calls == 1
    assert assignments.requested == ["101", "202"]
    assert grades.requested == ["101", "202"]
    assert announcements.requested == ["101", "202"]


def test_default_filter_skips_inactive_and_inaccessible_courses() -> None:
    course_list = [
        _course("101"),
        _course("202", active=False),
        _course("303", accessible=False),
    ]
    assignments = _Assignments({})
    grades = _Grades({})
    announcements = _Announcements({})

    result = AcademicOverviewService(
        _Courses(course_list), assignments, grades, announcements
    ).get_overview()

    assert [entry.course.id for entry in result.courses] == ["101"]
    assert assignments.requested == ["101"]
    assert grades.requested == ["101"]
    assert announcements.requested == ["101"]


def test_all_courses_includes_inactive_but_never_inaccessible() -> None:
    course_list = [
        _course("101"),
        _course("202", active=False),
        _course("303", accessible=False),
    ]
    assignments = _Assignments({})
    grades = _Grades({})
    announcements = _Announcements({})

    result = AcademicOverviewService(
        _Courses(course_list), assignments, grades, announcements
    ).get_overview(active_only=False)

    assert [entry.course.id for entry in result.courses] == ["101", "202"]
    assert assignments.requested == ["101", "202"]
    assert grades.requested == ["101", "202"]
    assert announcements.requested == ["101", "202"]


def test_empty_course_list_and_empty_course_data_are_explicit() -> None:
    empty_result = AcademicOverviewService(
        _Courses([]), _Assignments({}), _Grades({}), _Announcements({})
    ).get_overview()
    one_empty = AcademicOverviewService(
        _Courses([_course("101")]),
        _Assignments({}),
        _Grades({}),
        _Announcements({}),
    ).get_overview()

    assert empty_result.courses == ()
    assert len(one_empty.courses) == 1
    assert one_empty.courses[0].assignments == ()
    assert one_empty.courses[0].grade_items == ()
    assert one_empty.courses[0].announcements == ()
    assert one_empty.courses[0].retrieved_assignment_count == 0
    assert one_empty.courses[0].visible_grade_item_count == 0
    assert one_empty.courses[0].retrieved_announcement_count == 0


@pytest.mark.parametrize("kind", ["assignment", "grade", "announcement"])
def test_course_association_mismatch_fails_without_echoing_data(kind: str) -> None:
    assignments = _Assignments(
        {"101": [_assignment("PRIVATE-TASK", "999" if kind == "assignment" else "101")]}
    )
    grades = _Grades(
        {"101": [_grade("PRIVATE GRADE LABEL", "999" if kind == "grade" else "101")]}
    )
    announcements = _Announcements(
        {
            "101": [
                _announcement(
                    "PRIVATE-ANNOUNCEMENT",
                    "999" if kind == "announcement" else "101",
                )
            ]
        }
    )
    service = AcademicOverviewService(
        _Courses([_course("101")]), assignments, grades, announcements
    )

    with pytest.raises(ProviderEndpointChangedError) as exc_info:
        service.get_overview()

    message = str(exc_info.value)
    assert "course-mismatched" in message
    for secret in ("101", "999", "PRIVATE"):
        assert secret not in message


def test_provider_failure_aborts_without_fetching_later_courses() -> None:
    failure = NetworkUnavailableError("could not connect to provider")
    assignments = _Assignments({}, errors={"202": failure})
    grades = _Grades({})
    announcements = _Announcements({})
    service = AcademicOverviewService(
        _Courses([_course("101"), _course("202"), _course("303")]),
        assignments,
        grades,
        announcements,
    )

    with pytest.raises(NetworkUnavailableError):
        service.get_overview()

    assert assignments.requested == ["101", "202"]
    assert grades.requested == ["101"]
    assert announcements.requested == ["101"]


def test_grade_provider_failure_aborts_without_fetching_later_courses() -> None:
    failure = NetworkUnavailableError("could not connect to provider")
    assignments = _Assignments({})
    grades = _Grades({}, errors={"202": failure})
    announcements = _Announcements({})
    service = AcademicOverviewService(
        _Courses([_course("101"), _course("202"), _course("303")]),
        assignments,
        grades,
        announcements,
    )

    with pytest.raises(NetworkUnavailableError):
        service.get_overview()

    assert assignments.requested == ["101", "202"]
    assert grades.requested == ["101", "202"]
    assert announcements.requested == ["101"]


def test_announcement_provider_failure_aborts_without_fetching_later_courses() -> None:
    failure = NetworkUnavailableError("could not connect to provider")
    assignments = _Assignments({})
    grades = _Grades({})
    announcements = _Announcements({}, errors={"202": failure})
    service = AcademicOverviewService(
        _Courses([_course("101"), _course("202"), _course("303")]),
        assignments,
        grades,
        announcements,
    )

    with pytest.raises(NetworkUnavailableError):
        service.get_overview()

    assert assignments.requested == ["101", "202"]
    assert grades.requested == ["101", "202"]
    assert announcements.requested == ["101", "202"]


def test_invalid_normalized_course_data_is_replaced_with_fixed_safe_error() -> None:
    assignments = _Assignments(
        {}, errors={"101": ValueError("PRIVATE provider value must not escape")}
    )
    service = AcademicOverviewService(
        _Courses([_course("101")]),
        assignments,
        _Grades({}),
        _Announcements({}),
    )

    with pytest.raises(ProviderEndpointChangedError) as exc_info:
        service.get_overview()

    assert str(exc_info.value) == (
        "academic overview could not read normalized assignment data"
    )
    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value), exc_info.value, exc_info.value.__traceback__
        )
    )
    assert "PRIVATE" not in rendered


def test_invalid_announcement_data_is_replaced_with_fixed_safe_error() -> None:
    announcements = _Announcements(
        {}, errors={"101": ValueError("PRIVATE announcement value must not escape")}
    )
    service = AcademicOverviewService(
        _Courses([_course("101")]),
        _Assignments({}),
        _Grades({}),
        announcements,
    )

    with pytest.raises(ProviderEndpointChangedError) as exc_info:
        service.get_overview()

    assert str(exc_info.value) == (
        "academic overview could not read normalized announcement data"
    )
    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value), exc_info.value, exc_info.value.__traceback__
        )
    )
    assert "PRIVATE" not in rendered


def test_course_provider_failure_is_propagated_without_downstream_reads() -> None:
    assignments = _Assignments({})
    grades = _Grades({})
    announcements = _Announcements({})
    service = AcademicOverviewService(
        _Courses([], error=NetworkUnavailableError("course list unavailable")),
        assignments,
        grades,
        announcements,
    )

    with pytest.raises(NetworkUnavailableError):
        service.get_overview()

    assert assignments.requested == []
    assert grades.requested == []
    assert announcements.requested == []
