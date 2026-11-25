"""Deterministic resolution of visible courses from one complete course listing."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable

from d2l_cli.course_service import CourseService
from d2l_cli.models.course import Course


class CourseResolutionError(ValueError):
    """A query has no safe, unique accessible course result."""


class AmbiguousCourseError(CourseResolutionError):
    def __init__(self, matches: tuple[Course, ...]) -> None:
        super().__init__("course query is ambiguous")
        self.matches = matches


def _key(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def resolve_course(query: str, courses: list[Course]) -> Course:
    """Resolve by ID, code, name, prefix, then substring; never pick a tie."""
    if not isinstance(query, str) or len(query) > 200 or not query.strip():
        raise CourseResolutionError("course query is invalid")

    # Format controls can change what a person sees; pictographic symbols must
    # not disappear and accidentally turn a hostile query into a valid code.
    if any(
        (unicodedata.category(char).startswith("C") and char != "\t")
        or unicodedata.category(char) == "So"
        or (char.isspace() and char not in " \t")
        for char in query
    ):
        raise CourseResolutionError("course query is invalid")

    query = query.strip()
    key = _key(query)
    if not key:
        raise CourseResolutionError("course query is invalid")

    visible = [course for course in courses if course.is_active and course.can_access]

    if query.isascii() and query.isdecimal():
        numeric_matches = [course for course in courses if course.id == query]
        if not numeric_matches:
            raise CourseResolutionError("course not found")
        if len(numeric_matches) != 1 or numeric_matches[0] not in visible:
            raise CourseResolutionError("course is inactive or inaccessible")
        return numeric_matches[0]

    stages: tuple[Callable[[Course], bool], ...] = (
        lambda c: _key(c.code) == key,
        lambda c: _key(c.name) == key,
        lambda c: _key(c.code).startswith(key) or _key(c.name).startswith(key),
        lambda c: key in _key(c.code) or key in _key(c.name),
    )

    for matches_stage in stages:
        matches = tuple(course for course in visible if matches_stage(course))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousCourseError(matches)

    raise CourseResolutionError("course not found")


class CourseResolver:
    def __init__(self, service: CourseService) -> None:
        self._service = service

    def resolve(self, query: str) -> Course:
        return resolve_course(query, self._service.list_courses())