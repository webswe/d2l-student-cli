from __future__ import annotations

import dataclasses

import pytest

from d2l_cli.models.course import Course


def _course(**overrides: object) -> Course:
    defaults: dict[str, object] = {
        "id": "12345",
        "code": "CS-1301",
        "name": "Intro to Programming",
        "semester_name": "Fall 2026",
        "is_active": True,
        "can_access": True,
        "is_new_enrollment": False,
        "start_date": "2026-08-15T00:00:00.000Z",
        "end_date": "2026-12-15T00:00:00.000Z",
    }
    defaults.update(overrides)
    return Course(**defaults)  # type: ignore[arg-type]


def test_course_fields_round_trip() -> None:
    course = _course()
    assert course.id == "12345"
    assert course.code == "CS-1301"
    assert course.semester_name == "Fall 2026"


def test_course_is_immutable() -> None:
    course = _course()
    with pytest.raises(dataclasses.FrozenInstanceError):
        course.name = "changed"  # type: ignore[misc]


def test_course_allows_missing_dates() -> None:
    course = _course(start_date=None, end_date=None)
    assert course.start_date is None
    assert course.end_date is None
