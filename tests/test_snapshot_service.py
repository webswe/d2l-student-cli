"""Fictional, offline snapshot composition tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from d2l_cli.client.errors import AuthExpiredError, ProviderEndpointChangedError
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


def course(cid: str) -> Course:
    return Course(cid, "COURSE" + cid, "Fictional <course>", "Fall", True, True, False, None, None)


class Courses:
    def __init__(self, values: list[Course]) -> None:
        self.values = values
        self.calls = 0

    def list_courses(self) -> list[Course]:
        self.calls += 1
        return self.values


class Assignments:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.items: dict[str, tuple[Assignment, ...] | Exception] = {}

    def get_assignment_list(self, cid: str) -> AssignmentListPage:
        self.calls.append(cid)
        value = self.items.get(cid, ())
        if isinstance(value, Exception):
            raise value
        return AssignmentListPage(
            value,
            AssignmentCompleteness(AssignmentCompletenessStatus.UNKNOWN, len(value), None),
        )


class Grades:
    def get_grade_list(self, cid: str) -> GradeListPage:
        return GradeListPage(
            (GradeItem(cid, "Quiz", None, "A", None, FeedbackAvailability.UNKNOWN),),
            0,
            2,
            False,
        )


class Announcements:
    def get_announcement_list(self, cid: str) -> AnnouncementListPage:
        return AnnouncementListPage((Announcement(cid, "7", "<script>x</script>", None, "/safe"),))


def make_service(values: list[Course]) -> tuple[SnapshotService, Courses, Assignments]:
    courses = Courses(values)
    assignments = Assignments()
    return SnapshotService(courses, assignments, Grades(), Announcements()), courses, assignments


def test_multiple_courses_stable_order_and_source_selection() -> None:
    service, courses, assignments = make_service([course("2"), course("1")])
    assignments.items["1"] = (
        Assignment("3", "1", "Third", None, None),
        Assignment("1", "1", "First", None, None),
        Assignment("1", "1", "First", None, None),
    )
    result = service.get_snapshot(sources=("assignments", "grades"))
    assert [entry.course.id for entry in result.courses] == ["1", "2"]
    assert [a.id for a in result.courses[0].assignments] == ["1", "3"]
    assert result.courses[0].sources["announcements"].status == "not_requested"
    assert result.successful_sources == ("assignments", "grades")
    assert result.service_calls == 5
    assert courses.calls == 1 and assignments.calls == ["1", "2"]


def test_empty_course_and_partial_failure_are_distinct() -> None:
    service, _, assignments = make_service([course("1"), course("2")])
    assignments.items["2"] = ProviderEndpointChangedError("unsafe raw body")
    result = service.get_snapshot(sources=("assignments",))
    assert result.partial
    assert result.courses[0].assignments == ()
    assert result.courses[0].sources["assignments"].status == "success"
    assert result.courses[1].sources["assignments"].error_code == "ProviderEndpointChangedError"
    assert result.successful_sources == ()


def test_auth_expiry_aborts_without_partial_output() -> None:
    service, _, assignments = make_service([course("1")])
    assignments.items["1"] = AuthExpiredError("expired")
    with pytest.raises(AuthExpiredError):
        service.get_snapshot()


@pytest.mark.parametrize(
    "bad",
    [Assignment("5", "2", "Wrong", None, None), Assignment("x", "1", "Bad", None, None)],
)
def test_invalid_assignment_association_fails_only_that_source(bad: Assignment) -> None:
    service, _, assignments = make_service([course("1")])
    assignments.items["1"] = (bad,)
    result = service.get_snapshot()
    assert result.courses[0].sources["assignments"].status == "failed"
    assert result.courses[0].assignments == ()
    assert result.courses[0].sources["grades"].status == "success"


def test_conflicting_duplicate_identity_is_not_silently_chosen() -> None:
    service, _, assignments = make_service([course("1")])
    first = Assignment("5", "1", "One", None, None)
    assignments.items["1"] = (first, replace(first, name="Other"))
    result = service.get_snapshot(sources=("assignments",))
    assert result.courses[0].sources["assignments"].error_code == "InvalidNormalizedData"


def test_limits_unsupported_sources_and_course_identity() -> None:
    service, courses, _ = make_service([course("1"), course("2")])
    with pytest.raises(ValueError):
        service.get_snapshot(max_courses=1)
    with pytest.raises(ValueError):
        service.get_snapshot(sources=("posts",))
    with pytest.raises(ValueError):
        service.get_snapshot(max_courses=21)
    courses.values = [course("1"), course("1")]
    with pytest.raises(ProviderEndpointChangedError):
        service.get_snapshot()
