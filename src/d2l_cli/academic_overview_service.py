"""Unified read-only academic overview composed from verified services.

This layer introduces no provider endpoint. It lists courses once, then reads
the existing Assignment, Grades, and Announcements pages once per selected
course. Results remain grouped by their validated course association and
preserve provider ordering. Counts describe retrieved/displayed records only;
no academic status, deadline status, grade calculation, publication ordering,
or completeness claim is inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.models.announcement import (
    Announcement,
    AnnouncementCompleteness,
    AnnouncementListPage,
)
from d2l_cli.models.assignment import Assignment
from d2l_cli.models.assignment_completeness import (
    AssignmentCompleteness,
    AssignmentListPage,
)
from d2l_cli.models.course import Course
from d2l_cli.models.grade import GradeCompleteness, GradeItem, GradeListPage


class CourseLister(Protocol):
    def list_courses(self) -> list[Course]: ...


class AssignmentPageReader(Protocol):
    def get_assignment_list(self, course_id: str) -> AssignmentListPage: ...


class GradePageReader(Protocol):
    def get_grade_list(self, course_id: str) -> GradeListPage: ...


class AnnouncementPageReader(Protocol):
    def get_announcement_list(self, course_id: str) -> AnnouncementListPage: ...


@dataclass(frozen=True, slots=True)
class AcademicCourseOverview:
    course: Course
    assignments: tuple[Assignment, ...]
    assignment_completeness: AssignmentCompleteness
    grade_items: tuple[GradeItem, ...]
    grade_completeness: GradeCompleteness
    announcements: tuple[Announcement, ...]
    announcement_completeness: AnnouncementCompleteness

    @property
    def retrieved_assignment_count(self) -> int:
        return len(self.assignments)

    @property
    def recognized_due_date_count(self) -> int:
        return sum(assignment.due_date is not None for assignment in self.assignments)

    @property
    def without_recognized_due_date_count(self) -> int:
        return self.retrieved_assignment_count - self.recognized_due_date_count

    @property
    def visible_grade_item_count(self) -> int:
        return len(self.grade_items)

    @property
    def retrieved_announcement_count(self) -> int:
        return len(self.announcements)


@dataclass(frozen=True, slots=True)
class AcademicOverviewResult:
    courses: tuple[AcademicCourseOverview, ...]


class AcademicOverviewService:
    def __init__(
        self,
        course_lister: CourseLister,
        assignment_reader: AssignmentPageReader,
        grade_reader: GradePageReader,
        announcement_reader: AnnouncementPageReader,
    ) -> None:
        self._course_lister = course_lister
        self._assignment_reader = assignment_reader
        self._grade_reader = grade_reader
        self._announcement_reader = announcement_reader

    def get_overview(self, *, active_only: bool = True) -> AcademicOverviewResult:
        """Return grouped data for selected accessible courses.

        Any provider failure aborts the whole overview. Returning a mixture of
        successful and failed courses would make a partial result too easy to
        mistake for a complete academic overview.
        """
        results: list[AcademicCourseOverview] = []
        for course in self._course_lister.list_courses():
            if not course.can_access or (active_only and not course.is_active):
                continue

            try:
                assignment_page = self._assignment_reader.get_assignment_list(course.id)
            except ValueError:
                raise ProviderEndpointChangedError(
                    "academic overview could not read normalized assignment data"
                ) from None
            if any(assignment.course_id != course.id for assignment in assignment_page.assignments):
                raise ProviderEndpointChangedError(
                    "academic overview received course-mismatched assignment data"
                )

            try:
                grade_page = self._grade_reader.get_grade_list(course.id)
            except ValueError:
                raise ProviderEndpointChangedError(
                    "academic overview could not read normalized grade data"
                ) from None
            if any(item.course_id != course.id for item in grade_page.items):
                raise ProviderEndpointChangedError(
                    "academic overview received course-mismatched grade data"
                )

            try:
                announcement_page = self._announcement_reader.get_announcement_list(course.id)
            except ValueError:
                raise ProviderEndpointChangedError(
                    "academic overview could not read normalized announcement data"
                ) from None
            if any(announcement.course_id != course.id for announcement in announcement_page.items):
                raise ProviderEndpointChangedError(
                    "academic overview received course-mismatched announcement data"
                )

            results.append(
                AcademicCourseOverview(
                    course=course,
                    assignments=assignment_page.assignments,
                    assignment_completeness=assignment_page.completeness,
                    grade_items=grade_page.items,
                    grade_completeness=grade_page.completeness,
                    announcements=announcement_page.items,
                    announcement_completeness=announcement_page.completeness,
                )
            )

        return AcademicOverviewResult(courses=tuple(results))
