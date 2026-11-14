from __future__ import annotations

import pytest

from d2l_cli.client.errors import UnsupportedPaginationStateError
from d2l_cli.course_service import CourseService
from d2l_cli.models.course import Course
from d2l_cli.providers.brightspace_courses import CourseListPage

_COURSE = Course(
    id="1",
    code="CS-1301",
    name="Intro to Programming",
    semester_name="Fall 2026",
    is_active=True,
    can_access=True,
    is_new_enrollment=False,
    start_date=None,
    end_date=None,
)


class FakeProvider:
    def __init__(self, page: CourseListPage) -> None:
        self._page = page

    def list_courses(self) -> CourseListPage:
        return self._page


def test_list_courses_returns_courses_when_no_bookmark() -> None:
    service = CourseService(FakeProvider(CourseListPage(courses=(_COURSE,), bookmark=None)))  # type: ignore[arg-type]
    assert service.list_courses() == [_COURSE]


def test_list_courses_returns_empty_list() -> None:
    service = CourseService(FakeProvider(CourseListPage(courses=(), bookmark=None)))  # type: ignore[arg-type]
    assert service.list_courses() == []


def test_list_courses_raises_on_nonnull_bookmark() -> None:
    service = CourseService(
        FakeProvider(CourseListPage(courses=(_COURSE,), bookmark="opaque"))  # type: ignore[arg-type]
    )
    with pytest.raises(UnsupportedPaginationStateError):
        service.list_courses()
