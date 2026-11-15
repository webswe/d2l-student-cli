"""Normalized, read-only calendar models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class AssociatedEntity:
    """The Brightspace item (assignment, quiz, ...) an event belongs to, as reported."""

    entity_type: str | None
    entity_id: str | None


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """One calendar event.

    A timed event carries ``start_utc``/``end_utc`` (UTC instants, ``end_utc`` may be unknown);
    an all-day event carries ``start_day``/``end_day`` (local calendar days, end exclusive) and
    never an instant. ``recurring`` says the provider marks the event as a recurring series. Where
    the provider returned its occurrence list (LE 1.94+), each occurrence is its own record with an
    ``occurrence_id`` and ``occurrences_expanded`` is ``True``; otherwise a recurring event is only
    its *definition* (``occurrences_expanded`` is ``False``, ``occurrence_id`` is ``None``) and its
    start and end describe the series, not every occurrence. No occurrence is ever invented.
    """

    event_id: str
    course_id: str
    course_name: str | None
    course_code: str | None
    title: str
    description: str | None
    all_day: bool
    start_utc: str | None
    end_utc: str | None
    start_day: str | None
    end_day: str | None
    recurring: bool
    occurrence_id: str | None
    occurrences_expanded: bool
    event_type: str | None
    associated_entity: AssociatedEntity | None
    view_reference: str | None


@dataclass(frozen=True, slots=True)
class CalendarWindow:
    """A half-open UTC window ``[start, end)``."""

    start: datetime
    end: datetime


class RecurrenceMode(StrEnum):
    """How recurring events were obtained for a course."""

    OCCURRENCES = "occurrences"  # the documented occurrence route (LE 1.94+) was used
    DEFINITIONS_ONLY = "definitions_only"  # recurring events are series definitions only


@dataclass(frozen=True, slots=True)
class CourseEventPage:
    """The events of one course and whether the provider says more pages exist."""

    events: tuple[CalendarEvent, ...]
    has_more: bool
    mode: RecurrenceMode = RecurrenceMode.DEFINITIONS_ONLY
    occurrence_fallback: bool = False  # the occurrence route was refused or absent, base route used


@dataclass(frozen=True, slots=True)
class CourseFailure:
    """A course whose events could not be read; ``reason`` is a fixed code, never provider text."""

    course_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class CalendarListing:
    """Events for a window plus an honest account of what could not be read."""

    window: CalendarWindow
    events: tuple[CalendarEvent, ...]
    courses_requested: int
    courses_failed: tuple[CourseFailure, ...]
    truncated_courses: tuple[str, ...]
    duplicates_dropped: int
    recurrence_mode: str = "definitions_only"  # occurrences | definitions_only | mixed
    occurrence_fallback_courses: tuple[str, ...] = ()

    @property
    def unexpanded_recurring(self) -> int:
        """Recurring events shown only as definitions: their occurrences were not retrieved."""
        return sum(1 for e in self.events if e.recurring and not e.occurrences_expanded)

    @property
    def incomplete_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.courses_failed:
            reasons.append("course_failed")
        if self.truncated_courses:
            reasons.append("more_pages")
        if self.unexpanded_recurring:
            reasons.append("recurring_not_expanded")
        return tuple(reasons)

    @property
    def complete(self) -> bool:
        return not self.incomplete_reasons
