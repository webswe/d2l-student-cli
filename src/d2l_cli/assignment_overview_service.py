"""Cross-course assignment overview built from existing read-only services.

This layer introduces no provider endpoint of its own. It lists courses through
``CourseService`` and, for each selected accessible course, calls the existing
``AssignmentService.list_assignments()`` path. The resulting association is
checked before it is exposed so malformed provider data cannot silently label
an assignment with the wrong course.

Chronological ordering compares only the timezone-naive ``Assignment.due_date``
values already established by Phase 3A/3B. Assignments without a recognized due
date are placed last, in their original provider order. Nothing here compares a
due date with the local clock or infers overdue/due-soon/completion state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.models.assignment import Assignment
from d2l_cli.models.assignment_completeness import (
    AssignmentCompleteness,
    AssignmentListPage,
)
from d2l_cli.models.course import Course


class CourseLister(Protocol):
    def list_courses(self) -> list[Course]: ...


class AssignmentLister(Protocol):
    def list_assignments(self, course_id: str) -> list[Assignment]: ...

    def get_assignment_list(self, course_id: str) -> AssignmentListPage: ...


@dataclass(frozen=True, slots=True)
class AssignmentOverviewItem:
    course: Course
    assignment: Assignment


@dataclass(frozen=True, slots=True)
class CourseAssignmentCompleteness:
    course: Course
    completeness: AssignmentCompleteness


@dataclass(frozen=True, slots=True)
class AssignmentOverviewResult:
    items: tuple[AssignmentOverviewItem, ...]
    course_completeness: tuple[CourseAssignmentCompleteness, ...]


class AssignmentOverviewService:
    def __init__(self, course_lister: CourseLister, assignment_lister: AssignmentLister) -> None:
        self._course_lister = course_lister
        self._assignment_lister = assignment_lister

    def list_overview(
        self, *, active_only: bool = True, chronological: bool = False
    ) -> list[AssignmentOverviewItem]:
        """Return assignments across accessible courses.

        Active courses are selected by default to keep the overview focused and
        the number of Brightspace GETs bounded by the user's current course set.
        ``active_only=False`` includes every accessible course returned by the
        existing course provider.
        """
        items: list[AssignmentOverviewItem] = []
        for course in self._course_lister.list_courses():
            if not course.can_access or (active_only and not course.is_active):
                continue
            for assignment in self._assignment_lister.list_assignments(course.id):
                if assignment.course_id != course.id:
                    raise ProviderEndpointChangedError(
                        "assignment provider returned a course association that did not "
                        "match the requested course"
                    )
                items.append(AssignmentOverviewItem(course=course, assignment=assignment))

        if chronological:
            # ``list.sort`` is stable, so equal dates and undated assignments
            # retain the provider/course order in which they were observed.
            items.sort(
                key=lambda item: (
                    item.assignment.due_date is None,
                    item.assignment.due_date or datetime.max,
                )
            )
        return items

    def get_overview(
        self, *, active_only: bool = True, chronological: bool = False
    ) -> AssignmentOverviewResult:
        """Return the overview plus per-course response-boundary evidence."""
        items: list[AssignmentOverviewItem] = []
        assessments: list[CourseAssignmentCompleteness] = []
        for course in self._course_lister.list_courses():
            if not course.can_access or (active_only and not course.is_active):
                continue
            page = self._assignment_lister.get_assignment_list(course.id)
            assessments.append(
                CourseAssignmentCompleteness(
                    course=course,
                    completeness=page.completeness,
                )
            )
            for assignment in page.assignments:
                if assignment.course_id != course.id:
                    raise ProviderEndpointChangedError(
                        "assignment provider returned a course association that did not "
                        "match the requested course"
                    )
                items.append(AssignmentOverviewItem(course=course, assignment=assignment))

        if chronological:
            items.sort(
                key=lambda item: (
                    item.assignment.due_date is None,
                    item.assignment.due_date or datetime.max,
                )
            )
        return AssignmentOverviewResult(
            items=tuple(items),
            course_completeness=tuple(assessments),
        )
