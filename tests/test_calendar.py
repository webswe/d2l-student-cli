"""Calendar: provider parsing, window semantics, aggregation, DST rendering, and the CLI.

All data is fictional. The mocked transport stands in for Brightspace; nothing here contacts a
real institution or reads a real credential store.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

import d2l_cli.cli as cli_module
from d2l_cli.calendar_service import (
    CalendarService,
    failure_reason,
    format_instant,
    in_window,
    make_window,
    upcoming_window,
)
from d2l_cli.client.errors import (
    NotAuthenticatedError,
    PermissionDeniedError,
    ProviderEndpointChangedError,
    RateLimitedError,
    UnexpectedProviderError,
)
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.models.calendar import (
    CalendarEvent,
    CalendarWindow,
    CourseEventPage,
    RecurrenceMode,
)
from d2l_cli.models.course import Course
from d2l_cli.providers import valence_common as v
from d2l_cli.providers.brightspace_calendar import (
    BrightspaceCalendarProvider,
    parse_events,
    parse_occurrence_events,
)

BASE = "https://example.brightspace.com"
COURSE = "620000"
VERSIONS = [
    {"ProductCode": "le", "LatestVersion": "1.90", "SupportedVersions": ["1.74", "1.82", "1.90"]}
]
EVENTS_PATH = f"/d2l/api/le/1.90/{COURSE}/calendar/events/myEvents/"


def raw(event_id: int = 1, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "CalendarEventId": event_id,
        "OrgUnitId": int(COURSE),
        "Title": f"Event {event_id}",
        "Description": None,
        "StartDateTime": "2026-03-10T14:00:00.000Z",
        "EndDateTime": "2026-03-10T15:00:00.000Z",
        "StartDay": None,
        "EndDay": None,
        "IsAllDayEvent": False,
        "IsRecurring": False,
        "AssociatedEntity": None,
    }
    body.update(extra)
    return body


def window(start: str = "2026-03-01", end: str = "2026-04-01") -> CalendarWindow:
    return make_window(
        datetime.fromisoformat(start).date(),
        datetime.fromisoformat(end).date(),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )


def make_course(course_id: str = COURSE, *, active: bool = True, access: bool = True) -> Course:
    return Course(
        course_id,
        f"CODE{course_id}",
        f"Course {course_id}",
        "Spring",
        active,
        access,
        False,
        None,
        None,
    )


def client() -> D2LHttpClient:
    return D2LHttpClient(BASE, auth_header_provider=lambda: {"Cookie": "session=fictional"})


# --- shared helpers ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("supported", "expected"),
    [
        (["1.74", "1.82", "1.90"], "1.90"),
        (["1.82"], "1.82"),
        (["1.74", "1.75", "1.81", "1.121"], None),
    ],
)
def test_version_selection_enforces_the_documented_floor_and_ceiling(
    supported: list[str], expected: str | None
) -> None:
    payload = [{"ProductCode": "le", "SupportedVersions": supported}]
    if expected is None:
        with pytest.raises(ProviderEndpointChangedError, match="VERSION_UNSUPPORTED"):
            v.select_le_version(payload, "Calendar")
    else:
        assert v.select_le_version(payload, "Calendar") == expected


@pytest.mark.parametrize("bad", ["", "abc", "12 3", "١٢٣", "1" * 21, None, 5])
def test_ids_must_be_plain_numeric_strings(bad: object) -> None:
    with pytest.raises(ValueError, match="numeric"):
        v.require_id(bad, "course_id")


def test_relative_reference_rejects_absolute_and_unsafe_targets() -> None:
    assert v.relative_reference("/d2l/le/calendar/6606/event/1/view") is not None
    for bad in ("https://evil.example/x", "//evil.example/x", "d2l/x", "/a\\b", "/a@b", None, 3):
        assert v.relative_reference(bad) is None


# --- provider parsing ----------------------------------------------------------------------------


def test_a_timed_event_keeps_utc_instants_and_an_unknown_end_stays_unknown() -> None:
    page = parse_events([raw(1, EndDateTime=None)], course_id=COURSE)
    event = page.events[0]
    assert event.start_utc == "2026-03-10T14:00:00.000Z" and event.end_utc is None
    assert event.all_day is False and event.start_day is None


def test_an_all_day_event_is_a_day_span_and_never_an_instant() -> None:
    node = raw(
        2,
        IsAllDayEvent=True,
        StartDay="2026-03-10T00:00:00.000",
        EndDay="2026-03-11T00:00:00.000",
        StartDateTime="2026-03-10T05:00:00.000Z",
        EndDateTime="2026-03-11T05:00:00.000Z",
    )
    event = parse_events([node], course_id=COURSE).events[0]
    assert event.all_day and event.start_day == "2026-03-10" and event.end_day == "2026-03-11"
    assert event.start_utc is None and event.end_utc is None


def test_recurring_events_are_reported_once_and_never_expanded() -> None:
    page = parse_events(
        [raw(3, IsRecurring=True, RecurrenceInfo={"Pattern": "weekly"})], course_id=COURSE
    )
    assert len(page.events) == 1
    assert page.events[0].recurring is True and page.events[0].occurrences_expanded is False


def test_associated_entity_event_type_and_view_reference_are_normalized() -> None:
    node = raw(
        4,
        EventType=2,
        AssociatedEntity={"AssociatedEntityType": "Quiz", "AssociatedEntityId": 77},
        CalendarEventViewUrl="/d2l/le/calendar/620000/event/4/view",
    )
    event = parse_events([node], course_id=COURSE).events[0]
    assert event.event_type == "2"
    assert event.associated_entity is not None
    assert (event.associated_entity.entity_type, event.associated_entity.entity_id) == (
        "Quiz",
        "77",
    )
    assert event.view_reference == "/d2l/le/calendar/620000/event/4/view"


def test_titles_and_descriptions_are_sanitized_plain_text() -> None:
    node = raw(
        5,
        Title="Exam\x1b[31m RED\x1b[0m\x07",
        Description='<p>Bring <b>ID</b></p><script>alert(1)</script><img src="x">',
    )
    event = parse_events([node], course_id=COURSE).events[0]
    assert "\x1b" not in event.title and "\x07" not in event.title
    assert event.description is not None and "<" not in event.description
    assert "alert" not in event.description and "Bring ID" in event.description


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": []},
        "text",
        [raw(1), "not-an-object"],
        [raw(1, StartDateTime="2026-13-40T00:00:00Z")],
        [raw(1, StartDateTime="2026-03-10 14:00")],
        [raw(1, StartDateTime=None)],
        [raw(1, EndDateTime="2026-03-10T13:00:00.000Z")],
        [raw(1, IsAllDayEvent=True, StartDay=None)],
        [raw(1, IsAllDayEvent="yes")],
        [raw(1, Title=None)],
        [raw(1, CalendarEventId="abc")],
        [raw(1, OrgUnitId=999)],
        [raw(1), raw(1)],
        [raw(1, AssociatedEntity="oops")],
    ],
)
def test_malformed_responses_fail_closed(payload: object) -> None:
    with pytest.raises(ProviderEndpointChangedError, match="Calendar structure is unsupported"):
        parse_events(payload, course_id=COURSE)


def test_a_non_null_next_page_is_reported_not_followed() -> None:
    page = parse_events({"Objects": [raw(1)], "Next": "https://x/y"}, course_id=COURSE)
    assert page.has_more is True and len(page.events) == 1
    assert parse_events({"Objects": [], "Next": None}, course_id=COURSE) == CourseEventPage(
        (), False
    )


def test_an_empty_valid_response_is_an_empty_page_not_an_error() -> None:
    assert parse_events([], course_id=COURSE).events == ()


@respx.mock
def test_provider_issues_one_documented_get_with_utc_window_parameters() -> None:
    versions = respx.get(f"{BASE}/d2l/api/versions/").mock(
        return_value=httpx.Response(200, json=VERSIONS)
    )
    events = respx.get(f"{BASE}{EVENTS_PATH}").mock(
        return_value=httpx.Response(200, json={"Objects": [raw(1)], "Next": None})
    )
    page = BrightspaceCalendarProvider(client()).list_events(
        COURSE, window("2026-03-01", "2026-03-15")
    )
    assert len(page.events) == 1 and versions.call_count == 1 and events.call_count == 1
    request = events.calls.last.request
    assert request.method == "GET"
    assert request.url.params["startDateTime"] == "2026-03-01T00:00:00.000Z"
    assert request.url.params["endDateTime"] == "2026-03-15T00:00:00.000Z"
    assert request.headers["cookie"] == "session=fictional"


@respx.mock
def test_an_invalid_course_id_is_rejected_before_any_request() -> None:
    route = respx.route().mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(ValueError, match="numeric"):
        BrightspaceCalendarProvider(client()).list_events("../etc", window())
    assert route.call_count == 0


@respx.mock
def test_an_unsupported_api_version_fails_before_the_calendar_request() -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(
        return_value=httpx.Response(
            200, json=[{"ProductCode": "le", "SupportedVersions": ["1.74", "1.75"]}]
        )
    )
    calendar = respx.get(url__regex=r".*/calendar/.*").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(ProviderEndpointChangedError, match="VERSION_UNSUPPORTED"):
        BrightspaceCalendarProvider(client()).list_events(COURSE, window())
    assert calendar.call_count == 0


# --- window semantics ----------------------------------------------------------------------------


def event_at(start: str | None, end: str | None, **extra: Any) -> CalendarEvent:
    return parse_events(
        [raw(1, StartDateTime=start, EndDateTime=end, **extra)], course_id=COURSE
    ).events[0]


def test_timed_window_is_half_open_and_an_unknown_end_counts_only_by_its_start() -> None:
    w = window("2026-03-10", "2026-03-11")  # [03-10 00:00Z, 03-11 00:00Z)
    assert in_window(event_at("2026-03-10T00:00:00Z", "2026-03-10T01:00:00Z"), w)
    assert in_window(event_at("2026-03-09T23:00:00Z", "2026-03-10T00:30:00Z"), w)  # overlaps start
    assert not in_window(
        event_at("2026-03-09T23:00:00Z", "2026-03-10T00:00:00Z"), w
    )  # ends at start
    assert not in_window(
        event_at("2026-03-11T00:00:00Z", "2026-03-11T01:00:00Z"), w
    )  # starts at end
    assert in_window(event_at("2026-03-10T12:00:00Z", None), w)
    assert not in_window(event_at("2026-03-09T12:00:00Z", None), w)  # unknown end is not midnight


def all_day(start: str, end: str | None) -> CalendarEvent:
    node = raw(
        9, IsAllDayEvent=True, StartDay=start, EndDay=end, StartDateTime=None, EndDateTime=None
    )
    return parse_events([node], course_id=COURSE).events[0]


def test_all_day_events_use_exclusive_end_days_and_include_a_partial_final_day() -> None:
    w = window("2026-03-10", "2026-03-12")  # days 10 and 11
    assert in_window(all_day("2026-03-11", "2026-03-12"), w)
    assert not in_window(all_day("2026-03-12", "2026-03-13"), w)
    assert not in_window(all_day("2026-03-09", "2026-03-10"), w)  # exclusive end = day before
    assert in_window(all_day("2026-03-09", "2026-03-11"), w)
    assert in_window(all_day("2026-03-10", None), w) and not in_window(
        all_day("2026-03-12", None), w
    )
    partial = CalendarWindow(
        datetime(2026, 3, 10, tzinfo=UTC), datetime(2026, 3, 12, 6, tzinfo=UTC)
    )
    assert in_window(all_day("2026-03-12", "2026-03-13"), partial)


def test_windows_are_bounded_and_validated() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert make_window(None, None, now=now).end - make_window(
        None, None, now=now
    ).start == timedelta(days=30)
    with pytest.raises(ValueError, match="after"):
        make_window(datetime(2026, 3, 2).date(), datetime(2026, 3, 2).date(), now=now)
    with pytest.raises(ValueError, match="366"):
        make_window(datetime(2026, 1, 1).date(), datetime(2028, 1, 1).date(), now=now)
    for days in (0, 367, -1):
        with pytest.raises(ValueError, match="--days"):
            upcoming_window(days, now=now)


def test_upcoming_window_counts_elapsed_hours_so_daylight_saving_cannot_shift_it() -> None:
    now = datetime(2026, 3, 7, 12, 0, 0, 999, tzinfo=UTC)  # the US spring-forward is on 03-08
    w = upcoming_window(2, now=now)
    assert w.end - w.start == timedelta(hours=48) and w.start.microsecond == 0


class UsEastern2026(tzinfo):
    """Minimal US-Eastern rules for 2026 (DST 2026-03-08 02:00 to 2026-11-01 02:00 local)."""

    _START_UTC = datetime(2026, 3, 8, 7, tzinfo=UTC)
    _END_UTC = datetime(2026, 11, 1, 6, tzinfo=UTC)

    def fromutc(self, dt: datetime) -> datetime:
        utc = dt.replace(tzinfo=UTC)
        in_dst = self._START_UTC <= utc < self._END_UTC
        local = (utc + timedelta(hours=-4 if in_dst else -5)).replace(tzinfo=self)
        repeated_hour = self._END_UTC <= utc < self._END_UTC + timedelta(hours=1)
        return local.replace(fold=1 if repeated_hour else 0)

    def utcoffset(self, dt: datetime | None) -> timedelta:
        assert dt is not None
        naive = dt.replace(tzinfo=None)
        spring = datetime(2026, 3, 8, 3, 0)  # wall clock jumps 02:00 -> 03:00
        fall = datetime(2026, 11, 1, 1, 0)  # 01:00-02:00 occurs twice; fold=1 is the second
        dst = spring <= naive < fall or (fall <= naive < datetime(2026, 11, 1, 2) and dt.fold == 0)
        return timedelta(hours=-4 if dst else -5)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)


@pytest.mark.parametrize(
    ("utc", "rendered"),
    [
        ("2026-03-08T06:59:00.000Z", "2026-03-08 01:59 (UTC-05:00)"),
        ("2026-03-08T07:00:00.000Z", "2026-03-08 03:00 (UTC-04:00)"),
        ("2026-11-01T05:59:00.000Z", "2026-11-01 01:59 (UTC-04:00)"),
        ("2026-11-01T06:00:00.000Z", "2026-11-01 01:00 (UTC-05:00)"),
    ],
)
def test_local_rendering_follows_daylight_saving_rules_for_the_instant(
    utc: str, rendered: str
) -> None:
    assert format_instant(utc, UsEastern2026()) == rendered


# --- service aggregation -------------------------------------------------------------------------


class FakeProvider(BrightspaceCalendarProvider):
    def __init__(self, pages: Mapping[str, CourseEventPage | Exception]) -> None:
        self._pages = pages
        self.calls: list[str] = []

    def list_events(
        self,
        course_id: str,
        window: CalendarWindow,
        *,
        name: str | None = None,
        code: str | None = None,
    ) -> CourseEventPage:
        self.calls.append(course_id)
        outcome = self._pages[course_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def page(course_id: str, *events: dict[str, Any], more: bool = False) -> CourseEventPage:
    parsed = parse_events([dict(e, OrgUnitId=int(course_id)) for e in events], course_id=course_id)
    return CourseEventPage(parsed.events, more)


def test_events_across_courses_are_ordered_deterministically_with_all_day_first_on_a_day() -> None:
    provider = FakeProvider(
        {
            "1": page(
                "1", raw(10, Title="B", StartDateTime="2026-03-10T09:00:00Z", EndDateTime=None)
            ),
            "2": page(
                "2",
                raw(
                    11,
                    Title="A",
                    IsAllDayEvent=True,
                    StartDay="2026-03-10",
                    EndDay="2026-03-11",
                    StartDateTime=None,
                    EndDateTime=None,
                ),
                raw(12, Title="Early", StartDateTime="2026-03-09T09:00:00Z", EndDateTime=None),
            ),
        }
    )
    listing = CalendarService(provider).across([make_course("1"), make_course("2")], window())
    assert [e.event_id for e in listing.events] == ["12", "11", "10"]
    assert listing.complete and listing.courses_requested == 2


def test_a_failed_course_is_named_and_the_listing_is_marked_incomplete() -> None:
    provider = FakeProvider({"1": page("1", raw(10)), "2": PermissionDeniedError("denied")})
    listing = CalendarService(provider).across([make_course("1"), make_course("2")], window())
    assert [f.course_id for f in listing.courses_failed] == ["2"]
    assert listing.courses_failed[0].reason == "permission_denied"
    assert not listing.complete and len(listing.events) == 1


def test_if_every_course_fails_the_error_is_raised_not_an_empty_calendar() -> None:
    provider = FakeProvider(
        {"1": UnexpectedProviderError("boom"), "2": PermissionDeniedError("no")}
    )
    with pytest.raises(UnexpectedProviderError):
        CalendarService(provider).across([make_course("1"), make_course("2")], window())


def test_an_authentication_failure_aborts_instead_of_being_recorded_per_course() -> None:
    provider = FakeProvider({"1": page("1", raw(10)), "2": NotAuthenticatedError("expired")})
    with pytest.raises(NotAuthenticatedError):
        CalendarService(provider).across([make_course("1"), make_course("2")], window())


def test_no_courses_is_an_honest_empty_complete_listing() -> None:
    listing = CalendarService(FakeProvider({})).across([], window())
    assert listing.events == () and listing.complete and listing.courses_requested == 0


def test_more_pages_and_equal_event_ids_across_courses_are_reported() -> None:
    shared = raw(10)
    provider = FakeProvider({"1": page("1", shared, more=True), "2": page("2", shared)})
    listing = CalendarService(provider).across([make_course("1"), make_course("2")], window())
    assert listing.truncated_courses == ("1",) and not listing.complete
    assert listing.duplicates_dropped == 0 and len(listing.events) == 2
    assert {event.course_id for event in listing.events} == {"1", "2"}


def test_events_outside_the_window_are_filtered_even_if_the_provider_returns_them() -> None:
    provider = FakeProvider(
        {COURSE: page(COURSE, raw(1, StartDateTime="2027-01-01T00:00:00Z", EndDateTime=None))}
    )
    assert CalendarService(provider).for_course(COURSE, window()).events == ()


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (PermissionDeniedError("x"), "permission_denied"),
        (RateLimitedError("x", retry_after_seconds=3), "rate_limited"),
        (ProviderEndpointChangedError("x"), "unsupported_response"),
        (UnexpectedProviderError("x"), "provider_error"),
    ],
)
def test_failure_reasons_are_fixed_codes(error: Exception, code: str) -> None:
    assert failure_reason(error) == code  # type: ignore[arg-type]


# --- CLI -----------------------------------------------------------------------------------------

runner = CliRunner()


class _Auth:
    def __init__(self, headers: Mapping[str, str] | Exception) -> None:
        self._headers = headers

    def header_provider(self) -> Mapping[str, str]:
        if isinstance(self._headers, Exception):
            raise self._headers
        return self._headers


class _Courses:
    def __init__(self, courses: list[Course]) -> None:
        self._courses = courses

    def list_courses(self) -> list[Course]:
        return self._courses


def wire(
    monkeypatch: pytest.MonkeyPatch,
    provider: FakeProvider,
    *,
    courses: list[Course] | None = None,
    auth: object | None = None,
) -> None:
    monkeypatch.setattr(cli_module, "load_config", lambda: object())
    monkeypatch.setattr(
        cli_module, "_build_auth_service", lambda config: auth or _Auth({"Cookie": "x"})
    )
    monkeypatch.setattr(
        cli_module, "_build_calendar_service", lambda config, headers: CalendarService(provider)
    )
    monkeypatch.setattr(
        cli_module, "_build_course_service", lambda config, headers: _Courses(courses or [])
    )


def test_calendar_list_json_has_the_documented_stable_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(monkeypatch, FakeProvider({COURSE: page(COURSE, raw(1, Title="Midterm"))}))
    result = runner.invoke(
        cli_module.app,
        [
            "calendar",
            "list",
            "--course",
            COURSE,
            "--from",
            "2026-03-01",
            "--to",
            "2026-04-01",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stderr
    body = json.loads(result.stdout)["calendar"]
    assert list(body) == [
        "course_id",
        "window",
        "complete",
        "incomplete_reasons",
        "recurrence",
        "courses_requested",
        "courses_failed",
        "truncated_courses",
        "duplicates_dropped",
        "events",
    ]
    assert body["window"] == {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"}
    assert body["complete"] is True and body["courses_failed"] == []
    assert list(body["events"][0]) == [
        "event_id",
        "course_id",
        "course_name",
        "course_code",
        "title",
        "description",
        "all_day",
        "start_utc",
        "end_utc",
        "start_day",
        "end_day",
        "recurring",
        "occurrence_id",
        "occurrences_expanded",
        "event_type",
        "associated_entity",
        "view_reference",
    ]
    assert body["events"][0]["title"] == "Midterm" and result.stderr == ""


def test_calendar_upcoming_json_reports_partial_failures_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    soon = (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider = FakeProvider(
        {
            "1": page("1", raw(1, StartDateTime=soon, EndDateTime=None)),
            "2": PermissionDeniedError("denied"),
        }
    )
    wire(
        monkeypatch,
        provider,
        courses=[
            make_course("1"),
            make_course("2"),
            make_course("3", active=False),
            make_course("4", access=False),
        ],
    )
    result = runner.invoke(cli_module.app, ["calendar", "upcoming", "--days", "7", "--json"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)["calendar"]
    assert "course_id" not in body and body["complete"] is False
    assert body["courses_requested"] == 2 and body["courses_failed"] == [
        {"course_id": "2", "reason": "permission_denied"}
    ]
    assert provider.calls == ["1", "2"]  # inactive and inaccessible courses are not requested
    assert "could not be read" in result.stderr and "permission_denied" in result.stderr
    assert "Cookie" not in result.stderr


def test_all_courses_includes_inactive_accessible_courses(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider({"1": page("1"), "3": page("3")})
    wire(
        monkeypatch,
        provider,
        courses=[make_course("1"), make_course("3", active=False), make_course("4", access=False)],
    )
    result = runner.invoke(cli_module.app, ["calendar", "upcoming", "--all-courses", "--json"])
    assert result.exit_code == 0 and provider.calls == ["1", "3"]


def test_text_output_marks_recurring_and_all_day_and_warns_when_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = page(
        COURSE,
        raw(1, Title="Lecture", IsRecurring=True),
        raw(
            2,
            Title="Holiday",
            IsAllDayEvent=True,
            StartDay="2026-03-12",
            EndDay="2026-03-13",
            StartDateTime=None,
            EndDateTime=None,
        ),
    )
    wire(monkeypatch, FakeProvider({COURSE: CourseEventPage(events.events, True)}))
    result = runner.invoke(
        cli_module.app,
        ["calendar", "list", "--course", COURSE, "--from", "2026-03-01", "--to", "2026-04-01"],
    )
    assert result.exit_code == 0
    assert "(all day)" in result.stdout and "Holiday" in result.stdout
    assert "[recurring]" in result.stdout and "incomplete" in result.stderr


def test_an_empty_incomplete_text_result_does_not_claim_an_empty_calendar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(monkeypatch, FakeProvider({COURSE: CourseEventPage((), True)}))
    result = runner.invoke(
        cli_module.app,
        ["calendar", "list", "--course", COURSE, "--from", "2026-03-01", "--to", "2026-04-01"],
    )
    assert "not a complete answer" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["--from", "2026-3-1"],
        ["--from", "20260301"],
        ["--from", "2026-02-30"],
        ["--from", "2026-04-01", "--to", "2026-03-01"],
        ["--from", "2026-01-01", "--to", "2030-01-01"],
    ],
)
def test_invalid_windows_are_rejected_before_any_request(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    provider = FakeProvider({COURSE: page(COURSE)})
    wire(monkeypatch, provider)
    result = runner.invoke(
        cli_module.app, ["calendar", "list", "--course", COURSE, *args, "--json"]
    )
    assert result.exit_code == 1 and result.stdout == "" and provider.calls == []


def test_calendar_errors_exit_one_with_empty_stdout_and_no_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(
        monkeypatch,
        FakeProvider(
            {COURSE: ProviderEndpointChangedError("Calendar structure is unsupported (TITLE)")}
        ),
    )
    result = runner.invoke(cli_module.app, ["calendar", "list", "--course", COURSE, "--json"])
    assert result.exit_code == 1 and result.stdout == ""
    assert "unsupported" in result.stderr and "Cookie" not in result.stderr


def test_calendar_authentication_failure_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    wire(
        monkeypatch,
        FakeProvider({}),
        auth=_Auth(NotAuthenticatedError("No stored session; run 'd2l auth login'.")),
    )
    result = runner.invoke(cli_module.app, ["calendar", "upcoming", "--json"])
    assert result.exit_code == 1 and result.stdout == "" and "auth login" in result.stderr


def test_a_non_numeric_course_id_is_rejected_by_the_service_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Strict(FakeProvider):
        def list_events(self, course_id: str, window: CalendarWindow, **kw: Any) -> CourseEventPage:
            v.require_id(course_id, "course_id")
            return CourseEventPage((), False)

    wire(monkeypatch, Strict({}))
    result = runner.invoke(cli_module.app, ["calendar", "list", "--course", "abc", "--json"])
    assert result.exit_code == 1 and result.stdout == "" and "numeric" in result.stderr


# --- recurrence: occurrence route, fallback, honest completeness ---------------------------------

VERSIONS_94 = [
    {"ProductCode": "le", "SupportedVersions": ["1.74", "1.82", "1.94", "1.96"]},
]
OCC_PATH = "/d2l/api/le/1.96/calendar/events/myEventsWithOccurrences/"
BASE_96 = f"/d2l/api/le/1.96/{COURSE}/calendar/events/myEvents/"


def occ(recurrence_id: int, start: str, end: str | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "RecurrenceId": recurrence_id,
        "CalendarEventId": None,
        "StartDateTime": start,
        "EndDateTime": end,
        "StartDay": None,
        "EndDay": None,
        "IsAllDayEvent": False,
    }
    body.update(extra)
    return body


def with_occ(event: dict[str, Any], occurrences: object = "absent") -> dict[str, Any]:
    item: dict[str, Any] = {"EventDataInfo": event}
    if occurrences != "absent":
        item["Occurrences"] = occurrences
    return item


def recurring(event_id: int = 5, **extra: Any) -> dict[str, Any]:
    return raw(event_id, IsRecurring=True, RecurrenceInfo={"Pattern": "weekly"}, **extra)


def test_a_recurring_definition_alone_makes_the_listing_incomplete() -> None:
    provider = FakeProvider({COURSE: page(COURSE, recurring(5), raw(6))})
    listing = CalendarService(provider).for_course(COURSE, window())
    assert listing.unexpanded_recurring == 1 and listing.complete is False
    assert listing.incomplete_reasons == ("recurring_not_expanded",)


def test_the_occurrence_route_expands_each_documented_occurrence_once() -> None:
    payload = {
        "Objects": [
            with_occ(
                recurring(
                    5, StartDateTime="2026-03-03T14:00:00Z", EndDateTime="2026-03-03T15:00:00Z"
                ),
                [
                    occ(1, "2026-03-03T14:00:00Z", "2026-03-03T15:00:00Z"),
                    occ(2, "2026-03-10T14:00:00Z", "2026-03-10T15:00:00Z", CalendarEventId=5),
                ],
            ),
            with_occ(raw(6), None),
        ],
        "Next": None,
    }
    result = parse_occurrence_events(payload, course_id=COURSE)
    ids = [(e.event_id, e.occurrence_id, e.occurrences_expanded) for e in result.events]
    assert ids == [("5", "1", True), ("5", "2", True), ("6", None, False)]
    assert result.events[1].start_utc == "2026-03-10T14:00:00Z"  # the occurrence's own time
    assert result.mode is RecurrenceMode.OCCURRENCES


def test_a_recurring_event_without_an_occurrence_list_stays_a_flagged_definition() -> None:
    result = parse_occurrence_events([with_occ(recurring(5))], course_id=COURSE)
    assert [(e.occurrence_id, e.occurrences_expanded) for e in result.events] == [(None, False)]
    listing = CalendarService(FakeProvider({COURSE: result})).for_course(COURSE, window())
    assert listing.unexpanded_recurring == 1 and not listing.complete


def test_an_empty_occurrence_list_means_none_reported_and_is_not_an_invented_event() -> None:
    result = parse_occurrence_events([with_occ(recurring(5), [])], course_id=COURSE)
    assert result.events == ()


def test_all_day_occurrences_keep_day_semantics() -> None:
    day = occ(1, None, None, IsAllDayEvent=True, StartDay="2026-03-12", EndDay="2026-03-13")
    event = parse_occurrence_events([with_occ(recurring(5), [day])], course_id=COURSE).events[0]
    assert event.all_day and event.start_utc is None and event.end_day == "2026-03-13"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ([{"Occurrences": []}], "EVENT_DATA_SHAPE"),
        ([with_occ(recurring(5), "weekly")], "OCCURRENCES_SHAPE"),
        ([with_occ(recurring(5), ["x"])], "OCCURRENCE_SHAPE"),
        (
            [
                with_occ(
                    recurring(5), [occ(1, "2026-03-03T14:00:00Z"), occ(1, "2026-03-04T14:00:00Z")]
                )
            ],
            "DUPLICATE_OCCURRENCE",
        ),
        (
            [with_occ(recurring(5), [occ(1, "2026-03-03T14:00:00Z", CalendarEventId=9)])],
            "OCCURRENCE_EVENT_MISMATCH",
        ),
        ([with_occ(recurring(5), [occ("x", "2026-03-03T14:00:00Z")])], "OCCURRENCE_ID"),
        ([with_occ(recurring(5), [occ(1, "soon")])], "START"),
        ([with_occ(recurring(5), [occ(1, None)])], "TIMED_START"),
        (
            [with_occ(recurring(5), [occ(1, "2026-03-03T14:00:00Z", "2026-03-03T13:00:00Z")])],
            "END_BEFORE_START",
        ),
        ([with_occ(recurring(5, OrgUnitId=999), [])], "COURSE_MISMATCH"),
        ([with_occ(recurring(5), []), with_occ(recurring(5), [])], "DUPLICATE_EVENT"),
    ],
)
def test_malformed_occurrence_responses_fail_closed(payload: object, code: str) -> None:
    with pytest.raises(ProviderEndpointChangedError, match=code):
        parse_occurrence_events(payload, course_id=COURSE)


def test_distinct_occurrences_and_courses_are_never_collapsed() -> None:
    first = parse_occurrence_events(
        [
            with_occ(
                recurring(5, OrgUnitId=1),
                [occ(1, "2026-03-03T14:00:00Z"), occ(2, "2026-03-10T14:00:00Z")],
            )
        ],
        course_id="1",
    )
    again = parse_occurrence_events(
        [with_occ(recurring(5, OrgUnitId=2), [occ(1, "2026-03-03T14:00:00Z")])], course_id="2"
    )
    provider = FakeProvider({"1": first, "2": again})
    listing = CalendarService(provider).across([make_course("1"), make_course("2")], window())
    assert [(e.course_id, e.event_id, e.occurrence_id) for e in listing.events] == [
        ("1", "5", "1"), ("2", "5", "1"), ("1", "5", "2")
    ]
    assert listing.duplicates_dropped == 0 and listing.complete


def test_an_occurrence_spanning_the_window_start_is_included_by_overlap() -> None:
    w = window("2026-03-10", "2026-03-11")
    spanning = parse_occurrence_events(
        [with_occ(recurring(5), [occ(1, "2026-03-09T23:00:00Z", "2026-03-10T01:00:00Z")])],
        course_id=COURSE,
    ).events[0]
    assert in_window(spanning, w)


@respx.mock
def test_the_occurrence_route_is_used_from_le_1_94_with_the_documented_parameters() -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=VERSIONS_94))
    occurrences = respx.get(f"{BASE}{OCC_PATH}").mock(
        return_value=httpx.Response(200, json={"Objects": [with_occ(raw(6), None)], "Next": None})
    )
    base = respx.get(f"{BASE}{BASE_96}").mock(return_value=httpx.Response(200, json=[]))
    page = BrightspaceCalendarProvider(client()).list_events(
        COURSE, window("2026-03-01", "2026-03-15")
    )
    assert page.mode is RecurrenceMode.OCCURRENCES and not page.occurrence_fallback
    request = occurrences.calls.last.request
    assert request.method == "GET" and request.url.params["orgUnitIdsCSV"] == COURSE
    assert request.url.params["startDateTime"] == "2026-03-01T00:00:00.000Z"
    assert request.url.params["endDateTime"] == "2026-03-15T00:00:00.000Z"
    assert base.call_count == 0


@respx.mock
def test_before_le_1_94_the_occurrence_route_is_never_requested() -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=VERSIONS))
    respx.get(f"{BASE}{EVENTS_PATH}").mock(return_value=httpx.Response(200, json=[recurring(5)]))
    occurrences = respx.get(url__regex=r".*WithOccurrences.*").mock(
        return_value=httpx.Response(200, json=[])
    )
    page = BrightspaceCalendarProvider(client()).list_events(COURSE, window())
    assert occurrences.call_count == 0 and page.mode is RecurrenceMode.DEFINITIONS_ONLY


@respx.mock
@pytest.mark.parametrize("status", [403, 404])
def test_a_refused_or_missing_occurrence_route_falls_back_once_and_says_so(status: int) -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=VERSIONS_94))
    occurrences = respx.get(f"{BASE}{OCC_PATH}").mock(return_value=httpx.Response(status))
    base = respx.get(f"{BASE}{BASE_96}").mock(return_value=httpx.Response(200, json=[recurring(5)]))
    page = BrightspaceCalendarProvider(client()).list_events(COURSE, window())
    assert occurrences.call_count == 1 and base.call_count == 1  # finite: one extra request
    assert page.occurrence_fallback and page.mode is RecurrenceMode.DEFINITIONS_ONLY
    listing = CalendarService(FakeProvider({COURSE: page})).for_course(COURSE, window())
    assert listing.occurrence_fallback_courses == (COURSE,) and not listing.complete


@respx.mock
@pytest.mark.parametrize("status", [401, 429, 500])
def test_other_occurrence_route_failures_are_errors_not_silent_downgrades(status: int) -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=VERSIONS_94))
    respx.get(f"{BASE}{OCC_PATH}").mock(return_value=httpx.Response(status))
    base = respx.get(f"{BASE}{BASE_96}").mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(Exception) as raised:
        BrightspaceCalendarProvider(client()).list_events(COURSE, window())
    assert not isinstance(raised.value, AssertionError) and base.call_count == 0


@respx.mock
def test_a_next_page_cursor_on_the_occurrence_route_marks_the_course_truncated() -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=VERSIONS_94))
    respx.get(f"{BASE}{OCC_PATH}").mock(
        return_value=httpx.Response(200, json={"Objects": [], "Next": "https://x/y"})
    )
    page = BrightspaceCalendarProvider(client()).list_events(COURSE, window())
    assert page.has_more
    assert CalendarService(FakeProvider({COURSE: page})).for_course(
        COURSE, window()
    ).truncated_courses == (COURSE,)
# --- window boundaries, calendar edges, injected clock ----------------------------------------

# --- window boundaries, calendar edges, injected clock ---------------------------------------


def test_exact_boundaries_and_one_instant_either_side() -> None:
    w = window("2026-03-10", "2026-03-11")
    assert not in_window(event_at("2026-03-09T23:59:59Z", None), w)  # one second before start
    assert in_window(event_at("2026-03-10T00:00:00Z", None), w)  # exactly start (inclusive)
    assert in_window(event_at("2026-03-10T23:59:59.999Z", None), w)  # last instant inside
    assert not in_window(event_at("2026-03-11T00:00:00Z", None), w)  # exactly end (exclusive)
    assert not in_window(event_at("2026-03-11T00:00:00.001Z", None), w)


def test_an_event_crossing_midnight_overlaps_both_days_but_starts_in_only_one() -> None:
    crossing = event_at("2026-03-10T23:00:00Z", "2026-03-11T01:00:00Z")
    assert in_window(crossing, window("2026-03-10", "2026-03-11"))
    assert in_window(crossing, window("2026-03-11", "2026-03-12"))  # continues into it
    started_in = in_window(
        event_at("2026-03-11T00:30:00Z", None), window("2026-03-10", "2026-03-11")
    )
    assert started_in is False


def test_leap_days_are_real_and_impossible_february_dates_are_rejected() -> None:
    now = datetime(2027, 1, 1, tzinfo=UTC)
    leap = make_window(date(2028, 2, 28), date(2028, 3, 1), now=now)
    assert (leap.end - leap.start).days == 2  # 28th and the 29th
    assert in_window(event_at("2028-02-29T12:00:00Z", None), leap)
    for bad in ("2027-02-29", "2026-02-30", "2026-13-01", "2026-00-10"):
        with pytest.raises(ValueError, match="date"):
            cli_module._parse_calendar_date(bad, "--from")


def test_utc_offsets_never_change_the_instant() -> None:
    plus = timezone(timedelta(hours=5, minutes=30))
    assert format_instant("2026-03-10T14:00:00.000Z", plus) == "2026-03-10 19:30 (UTC+05:30)"
    assert format_instant("2026-03-10T14:00:00Z", UTC) == "2026-03-10 14:00 (UTC+00:00)"


def test_a_nonexistent_local_hour_is_never_produced_and_an_ambiguous_one_is_two_instants() -> None:
    zone = UsEastern2026()
    # 02:30 local on 2026-03-08 does not exist: no instant renders as 02:xx that day.
    for minutes in range(0, 24 * 60, 15):
        instant = datetime(2026, 3, 8, tzinfo=UTC) + timedelta(minutes=minutes)
        rendered = format_instant(instant.strftime("%Y-%m-%dT%H:%M:%SZ"), zone)
        assert not rendered.startswith("2026-03-08 02:")
    # 01:30 local on 2026-11-01 happens twice, as two different instants with different offsets.
    assert format_instant("2026-11-01T05:30:00Z", zone) == "2026-11-01 01:30 (UTC-04:00)"
    assert format_instant("2026-11-01T06:30:00Z", zone) == "2026-11-01 01:30 (UTC-05:00)"


@pytest.mark.parametrize("now", ["2026-03-07T12:00:00Z", "2026-10-31T12:00:00Z"])
def test_upcoming_with_an_injected_clock_across_a_dst_change_is_exactly_n_times_24_hours(
    monkeypatch: pytest.MonkeyPatch, now: str
) -> None:
    fixed = datetime.fromisoformat(now.replace("Z", "+00:00"))
    monkeypatch.setattr(cli_module, "_utc_now", lambda: fixed)
    provider = FakeProvider({"1": page("1")})
    wire(monkeypatch, provider, courses=[make_course("1")])
    result = runner.invoke(cli_module.app, ["calendar", "upcoming", "--days", "3", "--json"])
    body = json.loads(result.stdout)["calendar"]
    start = datetime.fromisoformat(body["window"]["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(body["window"]["end"].replace("Z", "+00:00"))
    assert start == fixed and end - start == timedelta(hours=72)


def test_the_default_list_window_uses_the_injected_clock_not_the_machine_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "_utc_now", lambda: datetime(2030, 6, 15, 9, 30, tzinfo=UTC))
    wire(monkeypatch, FakeProvider({COURSE: page(COURSE)}))
    result = runner.invoke(cli_module.app, ["calendar", "list", "--course", COURSE, "--json"])
    body = json.loads(result.stdout)["calendar"]
    assert body["window"] == {"start": "2030-06-15T00:00:00Z", "end": "2030-07-15T00:00:00Z"}


def test_json_reports_recurrence_mode_reasons_and_warns_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(monkeypatch, FakeProvider({COURSE: page(COURSE, recurring(5), raw(6))}))
    result = runner.invoke(
        cli_module.app,
        [
            "calendar",
            "list",
            "--course",
            COURSE,
            "--from",
            "2026-03-01",
            "--to",
            "2026-04-01",
            "--json",
        ],
    )
    body = json.loads(result.stdout)["calendar"]
    assert body["complete"] is False and body["incomplete_reasons"] == ["recurring_not_expanded"]
    assert body["recurrence"] == {
        "mode": "definitions_only",
        "unexpanded_recurring_events": 1,
        "occurrence_route_fallback_courses": [],
    }
    assert "series definitions" in result.stderr and "incomplete" in result.stderr
    recurring_event = next(e for e in body["events"] if e["recurring"])
    assert (
        recurring_event["occurrence_id"] is None
        and recurring_event["occurrences_expanded"] is False
    )


def test_partial_course_failures_still_report_complete_false_with_a_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(
        {
            "1": page("1", raw(1, StartDateTime="2026-03-10T10:00:00Z", EndDateTime=None)),
            "2": PermissionDeniedError("x"),
        }
    )
    wire(monkeypatch, provider, courses=[make_course("1"), make_course("2")])
    monkeypatch.setattr(cli_module, "_utc_now", lambda: datetime(2026, 3, 9, tzinfo=UTC))
    result = runner.invoke(cli_module.app, ["calendar", "upcoming", "--days", "5", "--json"])
    body = json.loads(result.stdout)["calendar"]
    assert body["complete"] is False and body["incomplete_reasons"] == ["course_failed"]
