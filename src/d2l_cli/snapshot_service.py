"""Bounded composition of the existing course-scoped academic readers.

No provider requests are made here beyond calls to the existing services.
The result is transient; callers decide how to render it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from d2l_cli.academic_overview_service import (
    AnnouncementPageReader,
    AssignmentPageReader,
    CourseLister,
    GradePageReader,
)
from d2l_cli.client.errors import (
    AuthExpiredError,
    D2LError,
    NotAuthenticatedError,
    ProviderEndpointChangedError,
    RateLimitedError,
)
from d2l_cli.course_resolver import resolve_course
from d2l_cli.models.announcement import Announcement
from d2l_cli.models.assignment import Assignment
from d2l_cli.models.course import Course
from d2l_cli.models.grade import GradeItem

SOURCES = ("assignments", "grades", "announcements")
MAX_COURSES = 20
MAX_RECORDS_PER_SOURCE = 10_000


@dataclass(frozen=True, slots=True)
class SourceResult:
    status: Literal["success", "failed", "not_requested"]
    completeness: str | None = None
    error_code: str | None = None
    observed_row_count: int | None = None
    page_size_ceiling: int | None = None


@dataclass(frozen=True, slots=True)
class SnapshotCourse:
    course: Course
    sources: dict[str, SourceResult]
    assignments: tuple[Assignment, ...] = ()
    grade_items: tuple[GradeItem, ...] = ()
    announcements: tuple[Announcement, ...] = ()


@dataclass(frozen=True, slots=True)
class Snapshot:
    generated_at: str
    requested_sources: tuple[str, ...]
    successful_sources: tuple[str, ...]
    courses: tuple[SnapshotCourse, ...]
    partial: bool
    service_calls: int


def _valid_id(value: str) -> bool:
    return bool(value) and value.isascii() and value.isdecimal()


def _error_code(error: Exception) -> str:
    # Never emit provider exception messages: some may include URLs or raw data.
    return type(error).__name__ if isinstance(error, D2LError) else "InvalidNormalizedData"


class SnapshotService:
    def __init__(
        self,
        course_lister: CourseLister,
        assignment_reader: AssignmentPageReader,
        grade_reader: GradePageReader,
        announcement_reader: AnnouncementPageReader,
    ) -> None:
        self._courses = course_lister
        self._assignments = assignment_reader
        self._grades = grade_reader
        self._announcements = announcement_reader

    def get_snapshot(
        self,
        *,
        sources: tuple[str, ...] = SOURCES,
        active_only: bool = True,
        max_courses: int = 10,
        course_queries: tuple[str, ...] = (),
        provider_order: bool = False,
    ) -> Snapshot:
        """Compose bounded course-scoped reads.

        ``course_queries`` narrows selection through the existing deterministic
        course resolver (ambiguous or unknown queries raise a ``ValueError``
        subclass before any per-course read). ``provider_order`` keeps the
        order in which the provider returned grade and announcement rows
        instead of the default deterministic sort; duplicate rows are always
        retained for grades.
        """
        if not 1 <= max_courses <= MAX_COURSES:
            raise ValueError("max_courses must be between 1 and 20")
        if (
            not sources
            or len(set(sources)) != len(sources)
            or any(s not in SOURCES for s in sources)
        ):
            raise ValueError("unsupported or repeated snapshot source")
        requested = tuple(s for s in SOURCES if s in sources)
        listed = self._courses.list_courses()
        if course_queries:
            resolved: dict[str, Course] = {}
            for query in course_queries:
                found = resolve_course(query, listed)
                resolved[found.id] = found
            selected = list(resolved.values())
        else:
            selected = [c for c in listed if c.can_access and (c.is_active or not active_only)]
        if len(selected) > max_courses:
            raise ValueError("selected courses exceed max_courses; increase the explicit limit")
        ids = [c.id for c in selected]
        if any(not _valid_id(cid) for cid in ids) or len(set(ids)) != len(ids):
            raise ProviderEndpointChangedError(
                "snapshot received invalid or duplicate course identity"
            )

        output: list[SnapshotCourse] = []
        calls = 1  # One course-list service invocation; not an HTTP request count.
        for course in sorted(selected, key=lambda c: (c.id, c.code, c.name)):
            statuses = {s: SourceResult("not_requested") for s in SOURCES}
            assignments: tuple[Assignment, ...] = ()
            grades: tuple[GradeItem, ...] = ()
            announcements: tuple[Announcement, ...] = ()
            for source in requested:
                calls += 1
                try:
                    if source == "assignments":
                        page = self._assignments.get_assignment_list(course.id)
                        if len(page.assignments) > MAX_RECORDS_PER_SOURCE or any(
                            a.course_id != course.id or not _valid_id(a.id)
                            for a in page.assignments
                        ):
                            raise ValueError("invalid assignment association")
                        by_id = {a.id: a for a in page.assignments}
                        if len(set(page.assignments)) != len(by_id):
                            raise ValueError("ambiguous assignment identity")
                        assignments = (
                            tuple(by_id.values())
                            if provider_order
                            else tuple(sorted(by_id.values(), key=lambda a: (a.id, a.name)))
                        )
                        statuses[source] = SourceResult(
                            "success",
                            page.completeness.status.value,
                            observed_row_count=page.completeness.observed_row_count,
                            page_size_ceiling=page.completeness.page_size_ceiling,
                        )
                    elif source == "grades":
                        page_g = self._grades.get_grade_list(course.id)
                        if len(page_g.items) > MAX_RECORDS_PER_SOURCE or any(
                            g.course_id != course.id for g in page_g.items
                        ):
                            raise ValueError("invalid grade association")
                        # Grades have no provider item ID. Identical display rows may
                        # represent distinct items, so retain every provider row.
                        grades = (
                            page_g.items
                            if provider_order
                            else tuple(
                                sorted(
                                    page_g.items,
                                    key=lambda g: (
                                        g.label,
                                        g.displayed_points or "",
                                        g.displayed_grade or "",
                                        g.displayed_weight or "",
                                        g.feedback_availability.value,
                                    ),
                                )
                            )
                        )
                        statuses[source] = SourceResult("success", page_g.completeness.value)
                    else:
                        page_n = self._announcements.get_announcement_list(course.id)
                        if len(page_n.items) > MAX_RECORDS_PER_SOURCE or any(
                            n.course_id != course.id or not _valid_id(n.announcement_id)
                            for n in page_n.items
                        ):
                            raise ValueError("invalid announcement association")
                        by_announcement_id = {n.announcement_id: n for n in page_n.items}
                        if len(set(page_n.items)) != len(by_announcement_id):
                            raise ValueError("ambiguous announcement identity")
                        announcements = (
                            tuple(by_announcement_id.values())
                            if provider_order
                            else tuple(
                                sorted(
                                    by_announcement_id.values(),
                                    key=lambda n: (n.announcement_id, n.title),
                                )
                            )
                        )
                        statuses[source] = SourceResult("success", page_n.completeness.value)
                except (AuthExpiredError, NotAuthenticatedError, RateLimitedError):
                    raise
                except (D2LError, ValueError) as error:
                    statuses[source] = SourceResult("failed", error_code=_error_code(error))
            output.append(SnapshotCourse(course, statuses, assignments, grades, announcements))

        successful = tuple(
            source
            for source in requested
            if all(entry.sources[source].status == "success" for entry in output)
        )
        return Snapshot(
            generated_at=datetime.now(UTC).isoformat(),
            requested_sources=requested,
            successful_sources=successful,
            courses=tuple(output),
            partial=any(
                entry.sources[source].status == "failed" for entry in output for source in requested
            ),
            service_calls=calls,
        )
