"""GET-only provider for the signed-in student's own calendar events in one course.

Route (documented Valence ``le`` calendar API, LE version 1.82 or later, scope
``calendar:my_events:read``): ``GET /d2l/api/le/{version}/{orgUnitId}/calendar/events/myEvents/``
with ``startDateTime`` and ``endDateTime`` (UTC). Occurrence-aware routes exist from 1.94, but
their response shape is not verifiable here, so recurring events are reported as recurring and
never expanded. Not confirmed against a live tenant; see docs/ACTIVITIES.md.

Fail-closed: a response that deviates from the documented shape, names another org unit, repeats
an event id, or exceeds a bound raises ``ProviderEndpointChangedError``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from d2l_cli.client.http import D2LHttpClient
from d2l_cli.models.calendar import (
    AssociatedEntity,
    CalendarEvent,
    CalendarWindow,
    CourseEventPage,
)
from d2l_cli.providers import valence_common as v

_LABEL = "Calendar"
MAX_EVENTS_PER_COURSE = 5_000


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


def parse_event(
    node: dict[str, Any], *, course_id: str, name: str | None, code: str | None
) -> CalendarEvent:
    event_id = v.id_of(node.get("CalendarEventId"), _LABEL, "EVENT_ID")
    org_unit = node.get("OrgUnitId")
    if org_unit is not None and v.id_of(org_unit, _LABEL, "ORG_UNIT") != course_id:
        raise v.changed(_LABEL, "COURSE_MISMATCH")
    all_day = _bool(node, "IsAllDayEvent")
    start_utc = v.utc_datetime(node.get("StartDateTime"), _LABEL, "START")
    end_utc = v.utc_datetime(node.get("EndDateTime"), _LABEL, "END")
    start_day = v.local_day(node.get("StartDay"), _LABEL, "START_DAY")
    end_day = v.local_day(node.get("EndDay"), _LABEL, "END_DAY")
    if all_day:
        if start_day is None:
            raise v.changed(_LABEL, "ALL_DAY_START")
        # An all-day event is a calendar-day span; an instant would imply an unknown zone.
        start_utc = end_utc = None
    else:
        if start_utc is None:
            raise v.changed(_LABEL, "TIMED_START")
        start_day = end_day = None
        if end_utc is not None and v.parse_utc(end_utc) < v.parse_utc(start_utc):
            raise v.changed(_LABEL, "END_BEFORE_START")
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
        occurrences_expanded=False,
        event_type=_event_type(node.get("EventType")),
        associated_entity=_entity(node),
        view_reference=v.relative_reference(node.get("CalendarEventViewUrl")),
    )


def parse_events(
    payload: object, *, course_id: str, name: str | None = None, code: str | None = None
) -> CourseEventPage:
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


def _utc_param(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


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
        path = f"/d2l/api/le/{self.api_version()}/{course}/calendar/events/myEvents/"
        response = self._client.get(
            path,
            params={
                "startDateTime": _utc_param(window.start),
                "endDateTime": _utc_param(window.end),
            },
        )
        return parse_events(response.json_body, course_id=course, name=name, code=code)
