from __future__ import annotations

import pytest

from d2l_cli.course_resolver import AmbiguousCourseError, CourseResolutionError, resolve_course
from d2l_cli.models.course import Course


def course(
    id: str, code: str, name: str, semester: str = "Fall", *, active: bool = True,
    access: bool = True,
) -> Course:
    return Course(id, code, name, semester, active, access, False, None, None)


COURSES = [
    course("1", "CS-3503", "Data Structures"),
    course("2", "MATH 1113", "Calculus I"),
    course("3", "CS-3503", "Data Structures", "Spring"),
    course("4", "BIO-1000", "Biology", active=False),
]


@pytest.mark.parametrize("query,expected", [
    ("1", "1"), ("MATH1113", "2"), ("calculus i", "2"),
    ("math 11", "2"), ("calc", "2"), (" 2 ", "2"), ("CS 3503", "ambiguous"),
    ("data structures", "ambiguous"), ("CS", "ambiguous"),
])
def test_resolution_order_and_ambiguity(query: str, expected: str) -> None:
    if expected == "ambiguous":
        with pytest.raises(AmbiguousCourseError) as exc:
            resolve_course(query, COURSES)
        assert {c.id for c in exc.value.matches} == {"1", "3"}
    else:
        assert resolve_course(query, COURSES).id == expected


@pytest.mark.parametrize(
    "query", ["999", "unknown", "", "\x1b[31m", "x" * 201, "BIO-1000", "CS🔥3503"]
)
def test_no_match_invalid_and_inaccessible(query: str) -> None:
    with pytest.raises(CourseResolutionError):
        resolve_course(query, COURSES)


def test_duplicate_code_does_not_hide_exact_id() -> None:
    assert resolve_course("3", COURSES).semester_name == "Spring"


def test_unicode_course_name_and_hidden_course_do_not_collide() -> None:
    courses = [
        course("5", "FR-1", "Études françaises"),
        course("6", "CHEM-1", "Chemistry", access=False),
    ]
    assert resolve_course("études", courses).id == "5"
    with pytest.raises(CourseResolutionError):
        resolve_course("CHEM-1", courses)
