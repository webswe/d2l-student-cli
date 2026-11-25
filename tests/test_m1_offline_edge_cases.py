"""Fictional provider edge cases for Milestone 1; no session or network access."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from d2l_cli.calendar_service import CalendarService
from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.course_resolver import AmbiguousCourseError, resolve_course
from d2l_cli.models.calendar import CalendarEvent, CalendarWindow, CourseEventPage
from d2l_cli.models.course import Course
from d2l_cli.providers.brightspace_calendar import parse_events
from d2l_cli.providers.brightspace_content import parse_content_tree
from d2l_cli.providers.brightspace_discussions import parse_forums
from d2l_cli.providers.brightspace_quizzes import parse_quizzes


@pytest.mark.parametrize(
    ("parse", "item"),
    [
        (
            parse_events,
            {
                "CalendarEventId": 1,
                "Title": "Fictional event",
                "StartDateTime": "2026-03-10T14:00:00Z",
            },
        ),
        (parse_quizzes, {"QuizId": 1, "Name": "Fictional quiz"}),
        (parse_forums, {"ForumId": 1, "Name": "Fictional forum"}),
    ],
)
@pytest.mark.parametrize("marker", ["empty", "missing"])
def test_invalid_next_marker_never_claims_a_complete_list(
    parse: object, item: dict, marker: str
) -> None:
    page = {"Objects": [item]}
    if marker == "empty":
        page["Next"] = ""
    with pytest.raises(ProviderEndpointChangedError, match="PAGE_SHAPE"):
        parse(page, course_id="100")  # type: ignore[operator]


def test_equal_event_ids_in_different_courses_remain_distinct() -> None:
    window = CalendarWindow(datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC))
    courses = [
        Course("100", "A", "Course A", "Spring", True, True, False, None, None),
        Course("200", "B", "Course B", "Spring", True, True, False, None, None),
    ]

    class Provider:
        def list_events(
            self, course_id: str, _window: CalendarWindow, **_kwargs: object
        ) -> CourseEventPage:
            event = CalendarEvent(
                event_id="7",
                course_id=course_id,
                course_name=None,
                course_code=None,
                title="Fictional event",
                description=None,
                all_day=False,
                start_utc="2026-03-10T14:00:00Z",
                end_utc="2026-03-10T15:00:00Z",
                start_day=None,
                end_day=None,
                recurring=False,
                occurrence_id=None,
                occurrences_expanded=False,
                event_type=None,
                associated_entity=None,
                view_reference=None,
            )
            return CourseEventPage((event,), has_more=False)

    result = CalendarService(Provider()).across(courses, window)  # type: ignore[arg-type]
    assert [(event.course_id, event.event_id) for event in result.events] == [
        ("100", "7"),
        ("200", "7"),
    ]
    assert result.duplicates_dropped == 0


def test_empty_module_and_unknown_topic_type_preserve_unknown_completeness() -> None:
    tree = parse_content_tree(
        {
            "Modules": [
                {"ModuleId": 1, "Title": "Empty", "Modules": [], "Topics": []},
                {
                    "ModuleId": 2,
                    "Title": "Week",
                    "Modules": [],
                    "Topics": [{"TopicId": 3, "Title": "Link", "TypeIdentifier": "ExternalLink"}],
                },
            ]
        },
        course_id="100",
    )
    assert tree.node_count == 3 and tree.completeness == "unknown"
    assert tree.modules[0].children == ()
    assert tree.modules[1].children[0].kind == "ExternalLink"


def test_unicode_casefold_collision_is_ambiguous_not_an_automatic_selection() -> None:
    courses = [
        Course("100", "STRASSE", "Street", "Spring", True, True, False, None, None),
        Course("200", "Straße", "Street", "Spring", True, True, False, None, None),
    ]
    with pytest.raises(AmbiguousCourseError) as error:
        resolve_course("straße", courses)
    assert {course.id for course in error.value.matches} == {"100", "200"}
