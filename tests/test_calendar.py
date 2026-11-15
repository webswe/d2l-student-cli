"""Calendar: provider parsing, window semantics, aggregation, DST rendering, and the CLI.

All data is fictional. The mocked transport stands in for Brightspace; nothing here contacts a
real institution or reads a real credential store.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, tzinfo
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
from d2l_cli.models.calendar import CalendarEvent, CalendarWindow, CourseEventPage
from d2l_cli.models.course import Course
from d2l_cli.providers import valence_common as v
from d2l_cli.providers.brightspace_calendar import (
    BrightspaceCalendarProvider,
    parse_events,
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


def test_more_pages_and_duplicate_events_across_courses_are_reported() -> None:
    shared = raw(10)
    provider = FakeProvider({"1": page("1", shared, more=True), "2": page("2", shared)})
    listing = CalendarService(provider).across([make_course("1"), make_course("2")], window())
    assert listing.truncated_courses == ("1",) and not listing.complete
    assert listing.duplicates_dropped == 1 and len(listing.events) == 1


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
