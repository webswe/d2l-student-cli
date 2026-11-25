"""Application service: calendar events for one course or across the accessible courses.

Window semantics (documented in docs/ACTIVITIES.md):

* A window is a half-open UTC interval ``[start, end)``.
* A timed event is inside when it starts before ``end`` and has not ended by ``start``; an event
  with an unknown end counts only when its start lies inside the window. A missing end is never
  treated as midnight or as a zero-length event.
* An all-day event is a calendar-day span with an exclusive end day and is compared by *days*,
  never converted to an instant. A window whose end is not at midnight includes that partial day.
* ``--days N`` means N elapsed 24-hour periods from now (UTC), so daylight-saving changes cannot
  shift the window.
* Ordering is deterministic: a timed event sorts by its UTC start; an all-day event sorts by its
  day at 00:00 UTC. That key is for ordering only and is not a real instant.
* A recurring event is complete only when its occurrences were retrieved (LE 1.94+ occurrence
  route). A recurring event shown only as a definition makes the listing incomplete
  (``recurring_not_expanded``): its start and end describe the series, not each occurrence.
* Events are de-duplicated by ``(event id, occurrence id)``, so distinct occurrences of one series
  are never collapsed and the same occurrence is never counted twice.

Nothing here hides a failure. A course that could not be read, or whose provider reported further
pages that are not followed, is listed explicitly and the listing is marked incomplete.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta, tzinfo

from d2l_cli.client.errors import (
    AuthExpiredError,
    D2LError,
    MalformedResponseError,
    NetworkUnavailableError,
    NotAuthenticatedError,
    PermissionDeniedError,
    ProviderEndpointChangedError,
    RateLimitedError,
    UnexpectedProviderError,
)
from d2l_cli.models.calendar import (
    CalendarEvent,
    CalendarListing,
    CalendarWindow,
    CourseEventPage,
    CourseFailure,
    RecurrenceMode,
)
from d2l_cli.models.course import Course
from d2l_cli.providers import valence_common as v
from d2l_cli.providers.brightspace_calendar import BrightspaceCalendarProvider

DEFAULT_DAYS = 30
MAX_WINDOW_DAYS = 366


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def make_window(
    start: date | None, end: date | None, *, now: datetime, default_days: int = DEFAULT_DAYS
) -> CalendarWindow:
    """A bounded UTC window from optional start/end *dates* (end exclusive)."""
    if start is None and end is None:
        start = now.astimezone(UTC).date()
    if start is None:
        assert end is not None
        start = end - timedelta(days=default_days)
    if end is None:
        end = start + timedelta(days=default_days)
    if end <= start:
        raise ValueError("calendar window end must be after its start")
    if (end - start).days > MAX_WINDOW_DAYS:
        raise ValueError(f"calendar window may not exceed {MAX_WINDOW_DAYS} days")
    return CalendarWindow(start=_midnight(start), end=_midnight(end))


def upcoming_window(days: int, *, now: datetime) -> CalendarWindow:
    """``days`` elapsed 24-hour periods starting at ``now`` (UTC)."""
    if not 1 <= days <= MAX_WINDOW_DAYS:
        raise ValueError(f"--days must be between 1 and {MAX_WINDOW_DAYS}")
    start = now.astimezone(UTC).replace(microsecond=0)
    return CalendarWindow(start=start, end=start + timedelta(days=days))


def _day(value: str) -> date:
    return date.fromisoformat(value)


def in_window(event: CalendarEvent, window: CalendarWindow) -> bool:
    if event.all_day:
        assert event.start_day is not None
        first = _day(event.start_day)
        window_first = window.start.date()
        # The window's last included day: its end day, or one more when the end is mid-day.
        window_end_day = window.end.date() + (
            timedelta(days=1) if window.end.time() != datetime.min.time() else timedelta(0)
        )
        if event.end_day is None:
            return window_first <= first < window_end_day
        return first < window_end_day and _day(event.end_day) > window_first
    assert event.start_utc is not None
    started = v.parse_utc(event.start_utc)
    if event.end_utc is None:
        return window.start <= started < window.end
    return started < window.end and v.parse_utc(event.end_utc) > window.start


def sort_key(event: CalendarEvent) -> tuple[datetime, int, str, str, str]:
    occurrence = event.occurrence_id or ""
    if event.all_day:
        assert event.start_day is not None
        return (_midnight(_day(event.start_day)), 0, event.title, event.event_id, occurrence)
    assert event.start_utc is not None
    return (v.parse_utc(event.start_utc), 1, event.title, event.event_id, occurrence)


def _mode(pages: list[CourseEventPage]) -> str:
    modes = {page.mode for page in pages}
    if not modes:
        return RecurrenceMode.DEFINITIONS_ONLY.value
    if len(modes) == 1:
        return next(iter(modes)).value
    return "mixed"


def format_instant(value: str, tz: tzinfo | None = None) -> str:
    """A UTC instant as local wall-clock time with its UTC offset (``tz`` None = system zone).

    The conversion is done on the instant, so a daylight-saving change is applied by the zone
    rules and never by arithmetic here.
    """
    moment = v.parse_utc(value).astimezone(tz)
    offset = moment.strftime("%z")
    return f"{moment.strftime('%Y-%m-%d %H:%M')} (UTC{offset[:3]}:{offset[3:]})"


def failure_reason(error: D2LError) -> str:
    """A fixed, privacy-safe code for a per-course failure (never provider text)."""
    if isinstance(error, PermissionDeniedError):
        return "permission_denied"
    if isinstance(error, RateLimitedError):
        return "rate_limited"
    if isinstance(error, NetworkUnavailableError):
        return "network_unavailable"
    if isinstance(error, (ProviderEndpointChangedError, MalformedResponseError)):
        return "unsupported_response"
    if isinstance(error, UnexpectedProviderError):
        return "provider_error"
    return "error"


class CalendarService:
    def __init__(self, provider: BrightspaceCalendarProvider) -> None:
        self._provider = provider

    def for_course(
        self,
        course_id: str,
        window: CalendarWindow,
        *,
        name: str | None = None,
        code: str | None = None,
    ) -> CalendarListing:
        """One course. A failure here is raised, never reported as an empty calendar."""
        page = self._provider.list_events(course_id, window, name=name, code=code)
        events = sorted((e for e in page.events if in_window(e, window)), key=sort_key)
        return CalendarListing(
            window=window,
            events=tuple(events),
            courses_requested=1,
            courses_failed=(),
            truncated_courses=(course_id,) if page.has_more else (),
            duplicates_dropped=0,
            recurrence_mode=_mode([page]),
            occurrence_fallback_courses=(course_id,) if page.occurrence_fallback else (),
        )

    def across(self, courses: Iterable[Course], window: CalendarWindow) -> CalendarListing:
        """Every given course. Per-course provider failures are recorded, not hidden.

        Authentication failures abort (the session itself is unusable). If every requested course
        fails the first error is raised, so a total outage is never reported as an empty calendar.
        """
        selected = list(courses)
        events: dict[tuple[str, str | None], CalendarEvent] = {}
        pages: list[CourseEventPage] = []
        fallback: list[str] = []
        failed: list[CourseFailure] = []
        truncated: list[str] = []
        duplicates = 0
        first_error: D2LError | None = None
        for course in selected:
            try:
                page = self._provider.list_events(
                    course.id, window, name=course.name, code=course.code
                )
            except (NotAuthenticatedError, AuthExpiredError):
                raise
            except D2LError as error:
                failed.append(CourseFailure(course_id=course.id, reason=failure_reason(error)))
                first_error = first_error or error
                continue
            pages.append(page)
            if page.occurrence_fallback:
                fallback.append(course.id)
            if page.has_more:
                truncated.append(course.id)
            for event in page.events:
                if not in_window(event, window):
                    continue
                key = (event.event_id, event.occurrence_id)
                if key in events:
                    duplicates += 1
                    continue
                events[key] = event
        if selected and len(failed) == len(selected) and first_error is not None:
            raise first_error
        return CalendarListing(
            window=window,
            events=tuple(sorted(events.values(), key=sort_key)),
            courses_requested=len(selected),
            courses_failed=tuple(failed),
            truncated_courses=tuple(truncated),
            duplicates_dropped=duplicates,
            recurrence_mode=_mode(pages),
            occurrence_fallback_courses=tuple(fallback),
        )
