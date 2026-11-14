"""Normalized course model.

Represents only the fields Phase 0.5 reconnaissance directly observed on
the ``mycourses`` endpoint and that Phase 1 needs (see docs/PROVIDER_NOTES.md,
"Session 4 / Live capture"). Deliberately excludes fields not needed yet
(``PinState``, ``TypeId``, ``FormattedImageLink``,
``CanSeeCourseInformation``, ``CanChangeCourseImage``,
``CourseInformationRoute``, ``CompletionDate``, ``DueDate``,
``CompletionStatus``).

Does NOT model a semester provider ID: Phase 0.5 established that
``SemesterName`` (a denormalized string) is the only semester-related field
observed on a course object -- no course-to-semester ``OrgUnitId``
relationship was observed, so none is invented here.

``start_date``/``end_date`` are kept as the provider's raw strings, not
parsed into ``datetime``. Phase 0.5 observed their JSON type (``str``) but
not their exact format/timezone semantics closely enough to parse them
without risking silently changing what the provider meant.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Course:
    id: str
    code: str
    name: str
    semester_name: str
    is_active: bool
    can_access: bool
    is_new_enrollment: bool
    start_date: str | None
    end_date: str | None
