"""Adapter over Brightspace's undocumented ``manageCourses`` web API.

This module is the ONLY place in the codebase that knows the raw
``mycourses`` JSON field names (``OrgUnitId``, ``Code``, ``Name``,
``SemesterName``, ``IsActive``, ``CanAccessCourse``, ``IsNewEnrollment``,
``StartDate``, ``EndDate``, ``Bookmark``). Everything above this layer
(``CourseService``, the CLI) only ever sees the normalized
``d2l_cli.models.Course`` model -- see docs/THREAT_MODEL.md/RECON.md: this
is an undocumented, internal Brightspace web API (not the documented
Valence API), so its shape may change without notice, and isolating the
parsing here means a schema change is a one-file fix.

Endpoint observed: ``GET /d2l/le/manageCourses/api/mycourses`` (Phase 0.5,
session 4; see docs/PROVIDER_NOTES.md for the full sanitized shape). This module
only ever calls ``D2LHttpClient.get()`` -- no POST/PUT/PATCH/DELETE.

``mysemesters`` is deliberately NOT called here: the normalized
``Course.semester_name`` field is already present on every ``mycourses``
record, and Phase 0.5 established no course->semester ``OrgUnitId``
relationship that would justify a second request for this phase's
`courses list` command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.models.course import Course

_MYCOURSES_PATH = "/d2l/le/manageCourses/api/mycourses"

# Fields this client relies on being present on every course object. Not
# every observed field is required -- only the ones the normalized Course
# model needs (see d2l_cli.models.course).
_REQUIRED_FIELDS = (
    "OrgUnitId",
    "Code",
    "Name",
    "SemesterName",
    "IsActive",
    "CanAccessCourse",
    "IsNewEnrollment",
)


@dataclass(frozen=True)
class CourseListPage:
    """One page of the ``mycourses`` response. ``bookmark`` is the raw
    (nullable) ``Bookmark`` value -- Phase 0.5 only ever observed it as
    null (single page); see ``d2l_cli.course_service`` for how a non-null
    bookmark is handled."""

    courses: tuple[Course, ...]
    bookmark: str | None


def _normalize_course_id(raw: object) -> str:
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise ProviderEndpointChangedError("mycourses course object had an invalid OrgUnitId")
    course_id = str(raw)
    if not course_id or not course_id.isascii() or not course_id.isdecimal():
        raise ProviderEndpointChangedError("mycourses course object had an invalid OrgUnitId")
    return course_id


def _required_string(raw: dict[str, Any], field: str) -> str:
    value = raw[field]
    if not isinstance(value, str):
        raise ProviderEndpointChangedError(f"mycourses course object had an invalid {field}")
    return value


def _required_boolean(raw: dict[str, Any], field: str) -> bool:
    value = raw[field]
    if not isinstance(value, bool):
        raise ProviderEndpointChangedError(f"mycourses course object had an invalid {field}")
    return value


def _optional_string(raw: dict[str, Any], field: str) -> str | None:
    value = raw.get(field)
    if value is not None and not isinstance(value, str):
        raise ProviderEndpointChangedError(f"mycourses course object had an invalid {field}")
    return value


def _parse_course(raw: object) -> Course:
    if not isinstance(raw, dict):
        raise ProviderEndpointChangedError("mycourses course entry was not an object")
    missing = [field for field in _REQUIRED_FIELDS if field not in raw]
    if missing:
        raise ProviderEndpointChangedError(
            f"mycourses course object is missing expected field(s): {missing}"
        )
    return Course(
        id=_normalize_course_id(raw["OrgUnitId"]),
        code=_required_string(raw, "Code"),
        name=_required_string(raw, "Name"),
        semester_name=_required_string(raw, "SemesterName"),
        is_active=_required_boolean(raw, "IsActive"),
        can_access=_required_boolean(raw, "CanAccessCourse"),
        is_new_enrollment=_required_boolean(raw, "IsNewEnrollment"),
        start_date=_optional_string(raw, "StartDate"),
        end_date=_optional_string(raw, "EndDate"),
    )


class BrightspaceCourseProvider:
    """GET-only adapter around the undocumented ``mycourses`` endpoint."""

    def __init__(self, client: D2LHttpClient) -> None:
        self._client = client

    def list_courses(self) -> CourseListPage:
        response = self._client.get(_MYCOURSES_PATH)
        body = response.json_body
        if not isinstance(body, dict) or "Courses" not in body:
            raise ProviderEndpointChangedError(
                "mycourses response did not contain the expected 'Courses' container"
            )
        raw_courses = body["Courses"]
        if not isinstance(raw_courses, list):
            raise ProviderEndpointChangedError("mycourses 'Courses' field was not a list")

        courses = tuple(_parse_course(item) for item in raw_courses)
        bookmark = body.get("Bookmark")
        return CourseListPage(courses=courses, bookmark=bookmark)
