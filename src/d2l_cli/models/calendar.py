"""Normalized, read-only calendar models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
    never an instant. Recurrence is only *reported*: ``recurring`` says the provider marks the
    event as recurring and ``occurrences_expanded`` is always ``False``, because no occurrence
    list is requested or invented.
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
    occurrences_expanded: bool
    event_type: str | None
    associated_entity: AssociatedEntity | None
    view_reference: str | None


@dataclass(frozen=True, slots=True)
class CalendarWindow:
    """A half-open UTC window ``[start, end)``."""

    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class CourseEventPage:
    """The events of one course and whether the provider says more pages exist."""

    events: tuple[CalendarEvent, ...]
    has_more: bool


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

    @property
    def complete(self) -> bool:
        return not self.courses_failed and not self.truncated_courses
