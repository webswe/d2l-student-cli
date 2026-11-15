"""GET-only provider for the signed-in student's own calendar events in one course.

Routes (documented Valence ``le`` calendar API):

* LE 1.94 or later (scope ``calendar:my_events:read``): ``GET
  /d2l/api/le/{version}/calendar/events/myEventsWithOccurrences/`` with ``orgUnitIdsCSV`` (this
  one course), ``startDateTime`` (inclusive) and ``endDateTime`` (exclusive). It returns
  ``EventWithOccurrencesInfo`` records: an ``EventDataInfo`` plus its ``Occurrences``.
* LE 1.82 or later: ``GET /d2l/api/le/{version}/{orgUnitId}/calendar/events/myEvents/`` with
  ``startDateTime`` and ``endDateTime``. It returns ``EventDataInfo`` only, so a recurring event
  appears once, as a *definition*; its occurrences are not retrieved and none is invented.

Strategy: use the occurrence route when the tenant lists LE 1.94 or later. If that route is
refused (403) or absent (404), fall back exactly once to the base route for that course; the
fallback is recorded in the result (``occurrence_fallback``) and the recurring events it yields are
counted as unexpanded, so the listing is reported incomplete. Any other failure is an error, never
a silent downgrade. The paging contract of the occurrence route's ``ObjectListPage`` is not
documented, so a non-null ``Next`` marks the page incomplete and is never followed.

Fail-closed: a response that deviates from the documented shape, names another org unit or
event, repeats an event or occurrence id, or exceeds a bound raises
``ProviderEndpointChangedError``. Not confirmed against a live tenant; see docs/ACTIVITIES.md.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime
from typing import Any

from d2l_cli.client.errors import PermissionDeniedError, UnexpectedProviderError
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.models.calendar import (
    AssociatedEntity,
    CalendarEvent,
    CalendarWindow,
    CourseEventPage,
    RecurrenceMode,
)
from d2l_cli.providers import valence_common as v

_LABEL = "Calendar"
MAX_EVENTS_PER_COURSE = 5_000
MAX_OCCURRENCES_PER_EVENT = 5_000
OCCURRENCE_MIN_MINOR = 94
_NOT_FOUND_RE = re.compile(r"unexpected status 404\Z")


def _bool(node: dict[str, Any], key: str, *, default: bool = False) -> bool:
    value = node.get(key, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise v.changed(_LABEL, f"{key}_FLAG")
    return value


def _entity(node: dict[str, Any]) -> AssociatedEntity | None:
    raw = node.get("AssociatedEntity")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise v.changed(_LABEL, "ENTITY_SHAPE")
    entity_id = raw.get("AssociatedEntityId")
    return AssociatedEntity(
        entity_type=v.optional_text(
            raw.get("AssociatedEntityType"), _LABEL, "ENTITY_TYPE", limit=100
        ),
        entity_id=None if entity_id is None else v.id_of(entity_id, _LABEL, "ENTITY_ID"),
    )


def _event_type(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise v.changed(_LABEL, "EVENT_TYPE")
    return v.clean_text(str(value), _LABEL, "EVENT_TYPE", limit=50) or None


def _times(node: dict[str, Any]) -> tuple[bool, str | None, str | None, str | None, str | None]:
    """``(all_day, start_utc, end_utc, start_day, end_day)`` with the all-day/timed rules applied.

    An all-day record is a calendar-day span and never carries an instant (that would imply an
    unknown zone); a timed record always has a start, and an unknown end stays ``None``.
    """
    all_day = _bool(node, "IsAllDayEvent")
    start_utc = v.utc_datetime(node.get("StartDateTime"), _LABEL, "START")
    end_utc = v.utc_datetime(node.get("EndDateTime"), _LABEL, "END")
    start_day = v.local_day(node.get("StartDay"), _LABEL, "START_DAY")
    end_day = v.local_day(node.get("EndDay"), _LABEL, "END_DAY")
    if all_day:
        if start_day is None:
            raise v.changed(_LABEL, "ALL_DAY_START")
        return True, None, None, start_day, end_day
    if start_utc is None:
        raise v.changed(_LABEL, "TIMED_START")
    if end_utc is not None and v.parse_utc(end_utc) < v.parse_utc(start_utc):
        raise v.changed(_LABEL, "END_BEFORE_START")
    return False, start_utc, end_utc, None, None


def parse_event(
    node: dict[str, Any], *, course_id: str, name: str | None, code: str | None
) -> CalendarEvent:
    event_id = v.id_of(node.get("CalendarEventId"), _LABEL, "EVENT_ID")
    org_unit = node.get("OrgUnitId")
    if org_unit is not None and v.id_of(org_unit, _LABEL, "ORG_UNIT") != course_id:
        raise v.changed(_LABEL, "COURSE_MISMATCH")
    all_day, start_utc, end_utc, start_day, end_day = _times(node)
    return CalendarEvent(
        event_id=event_id,
        course_id=course_id,
        course_name=name,
        course_code=code,
        title=v.clean_text(node.get("Title"), _LABEL, "TITLE"),
        description=v.rich_text(node.get("Description"), _LABEL, "DESCRIPTION"),
        all_day=all_day,
        start_utc=start_utc,
        end_utc=end_utc,
        start_day=start_day,
        end_day=end_day,
        recurring=_bool(node, "IsRecurring"),
        occurrence_id=None,
        occurrences_expanded=False,
        event_type=_event_type(node.get("EventType")),
        associated_entity=_entity(node),
        view_reference=v.relative_reference(node.get("CalendarEventViewUrl")),
    )


def parse_events(
    payload: object, *, course_id: str, name: str | None = None, code: str | None = None
) -> CourseEventPage:
    """The base route: event definitions only (a recurring event appears once, unexpanded)."""
    items, has_next = v.object_page(payload, _LABEL, max_items=MAX_EVENTS_PER_COURSE)
    events: list[CalendarEvent] = []
    seen: set[str] = set()
    for item in items:
        event = parse_event(item, course_id=course_id, name=name, code=code)
        if event.event_id in seen:
            raise v.changed(_LABEL, "DUPLICATE_EVENT")
        seen.add(event.event_id)
        events.append(event)
    return CourseEventPage(events=tuple(events), has_more=has_next)


def _expand(event: CalendarEvent, occurrences: object) -> list[CalendarEvent]:
    """One record per documented occurrence of a recurring ``event``; never an invented one."""
    if not isinstance(occurrences, list) or len(occurrences) > MAX_OCCURRENCES_PER_EVENT:
        raise v.changed(_LABEL, "OCCURRENCES_SHAPE")
    records: list[CalendarEvent] = []
    seen: set[str] = set()
    for raw in occurrences:
        if not isinstance(raw, dict):
            raise v.changed(_LABEL, "OCCURRENCE_SHAPE")
        occurrence_id = v.id_of(raw.get("RecurrenceId"), _LABEL, "OCCURRENCE_ID")
        owner = raw.get("CalendarEventId")  # may be null inside an event that already names it
        if owner is not None and v.id_of(owner, _LABEL, "OCCURRENCE_EVENT") != event.event_id:
            raise v.changed(_LABEL, "OCCURRENCE_EVENT_MISMATCH")
        if occurrence_id in seen:
            raise v.changed(_LABEL, "DUPLICATE_OCCURRENCE")
        seen.add(occurrence_id)
        all_day, start_utc, end_utc, start_day, end_day = _times(raw)
        records.append(
            dataclasses.replace(
                event,
                all_day=all_day,
                start_utc=start_utc,
                end_utc=end_utc,
                start_day=start_day,
                end_day=end_day,
                occurrence_id=occurrence_id,
                occurrences_expanded=True,
            )
        )
    return records


def parse_occurrence_events(
    payload: object, *, course_id: str, name: str | None = None, code: str | None = None
) -> CourseEventPage:
    """The occurrence route: ``EventWithOccurrencesInfo`` records.

    A non-recurring event is one record. A recurring event with an ``Occurrences`` list becomes
    one record per occurrence (an empty list means the provider reports none in the window). A
    recurring event whose ``Occurrences`` is missing stays a definition, flagged unexpanded.
    """
    items, has_next = v.object_page(payload, _LABEL, max_items=MAX_EVENTS_PER_COURSE)
    records: list[CalendarEvent] = []
    seen_events: set[str] = set()
    for item in items:
        data = item.get("EventDataInfo")
        if not isinstance(data, dict):
            raise v.changed(_LABEL, "EVENT_DATA_SHAPE")
        event = parse_event(data, course_id=course_id, name=name, code=code)
        if event.event_id in seen_events:
            raise v.changed(_LABEL, "DUPLICATE_EVENT")
        seen_events.add(event.event_id)
        if not event.recurring or item.get("Occurrences") is None:
            records.append(event)
        else:
            records.extend(_expand(event, item["Occurrences"]))
    if len(records) > MAX_EVENTS_PER_COURSE:
        raise v.changed(_LABEL, "ITEM_LIMIT")
    return CourseEventPage(
        events=tuple(records), has_more=has_next, mode=RecurrenceMode.OCCURRENCES
    )


def _utc_param(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _route_absent(error: Exception) -> bool:
    """Whether the provider said the occurrence route is refused (403) or missing (404)."""
    if isinstance(error, PermissionDeniedError):
        return True
    return isinstance(error, UnexpectedProviderError) and bool(_NOT_FOUND_RE.search(str(error)))


class BrightspaceCalendarProvider:
    """Read one course's own calendar events for a UTC window."""

    def __init__(self, client: D2LHttpClient) -> None:
        self._client = client
        self._version: str | None = None

    def api_version(self) -> str:
        if self._version is None:
            response = self._client.get(v.VERSIONS_PATH)
            self._version = v.select_le_version(response.json_body, _LABEL)
        return self._version

    def list_events(
        self,
        course_id: str,
        window: CalendarWindow,
        *,
        name: str | None = None,
        code: str | None = None,
    ) -> CourseEventPage:
        course = v.require_id(course_id, "course_id")
        version = self.api_version()
        window_params = {
            "startDateTime": _utc_param(window.start),
            "endDateTime": _utc_param(window.end),
        }
        if v.minor_of(version) >= OCCURRENCE_MIN_MINOR:
            path = f"/d2l/api/le/{version}/calendar/events/myEventsWithOccurrences/"
            try:
                response = self._client.get(path, params={"orgUnitIdsCSV": course, **window_params})
            except (PermissionDeniedError, UnexpectedProviderError) as error:
                if not _route_absent(error):
                    raise
                page = self._base(course, version, window_params, name, code)
                return dataclasses.replace(page, occurrence_fallback=True)
            return parse_occurrence_events(
                response.json_body, course_id=course, name=name, code=code
            )
        return self._base(course, version, window_params, name, code)

    def _base(
        self,
        course: str,
        version: str,
        window_params: dict[str, str],
        name: str | None,
        code: str | None,
    ) -> CourseEventPage:
        path = f"/d2l/api/le/{version}/{course}/calendar/events/myEvents/"
        response = self._client.get(path, params=window_params)
        return parse_events(response.json_body, course_id=course, name=name, code=code)
