from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.client.http import D2LResponse
from d2l_cli.providers.brightspace_courses import BrightspaceCourseProvider

# Sanitized shape observed in Phase 0.5 reconnaissance session 4 (see
# docs/PROVIDER_NOTES.md, "Live capture -- success"). Values here are synthetic.
OBSERVED_MYCOURSES_BODY: dict[str, Any] = {
    "Courses": [
        {
            "OrgUnitId": "778899",
            "PinState": None,
            "TypeId": 3,
            "Name": "Intro to Programming",
            "Code": "CS-1301-01",
            "IsActive": True,
            "StartDate": "2026-08-15T00:00:00.000Z",
            "EndDate": "2026-12-15T00:00:00.000Z",
            "FormattedImageLink": "https://example.brightspace.com/images/course.jpg",
            "SemesterName": "Fall 2026",
            "CanSeeCourseInformation": True,
            "CanChangeCourseImage": False,
            "CourseInformationRoute": "/d2l/lms/manageCourses/course_information.d2l",
            "IsNewEnrollment": False,
            "CompletionDate": None,
            "DueDate": None,
            "CanAccessCourse": True,
            "CompletionStatus": None,
        }
    ],
    "Bookmark": None,
    "Sort": "Name",
}


class FakeD2LHttpClient:
    def __init__(self, body: Any, *, status_code: int = 200) -> None:
        self._body = body
        self._status_code = status_code
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []

    def get(self, path: str, *, params: Mapping[str, str] | None = None) -> D2LResponse:
        self.calls.append((path, params))
        return D2LResponse(status_code=self._status_code, json_body=self._body)


def test_list_courses_parses_observed_shape() -> None:
    client = FakeD2LHttpClient(OBSERVED_MYCOURSES_BODY)
    page = BrightspaceCourseProvider(client).list_courses()  # type: ignore[arg-type]

    assert page.bookmark is None
    assert len(page.courses) == 1
    course = page.courses[0]
    assert course.id == "778899"
    assert course.code == "CS-1301-01"
    assert course.name == "Intro to Programming"
    assert course.semester_name == "Fall 2026"
    assert course.is_active is True
    assert course.can_access is True
    assert course.is_new_enrollment is False
    assert course.start_date == "2026-08-15T00:00:00.000Z"
    assert course.end_date == "2026-12-15T00:00:00.000Z"


def test_list_courses_calls_only_mycourses_endpoint() -> None:
    client = FakeD2LHttpClient(OBSERVED_MYCOURSES_BODY)
    BrightspaceCourseProvider(client).list_courses()  # type: ignore[arg-type]
    assert client.calls == [("/d2l/le/manageCourses/api/mycourses", None)]


def test_list_courses_handles_nullable_dates() -> None:
    body = {
        "Courses": [
            {
                "OrgUnitId": "1",
                "Name": "No Dates",
                "Code": "X-1",
                "SemesterName": "Fall 2026",
                "IsActive": True,
                "CanAccessCourse": True,
                "IsNewEnrollment": True,
                "StartDate": None,
                "EndDate": None,
            }
        ],
        "Bookmark": None,
    }
    client = FakeD2LHttpClient(body)
    page = BrightspaceCourseProvider(client).list_courses()  # type: ignore[arg-type]
    assert page.courses[0].start_date is None
    assert page.courses[0].end_date is None


def test_list_courses_normalizes_integer_org_unit_id_to_decimal_string() -> None:
    course = dict(OBSERVED_MYCOURSES_BODY["Courses"][0])
    course["OrgUnitId"] = 778899
    client = FakeD2LHttpClient({"Courses": [course], "Bookmark": None})

    page = BrightspaceCourseProvider(client).list_courses()  # type: ignore[arg-type]

    assert page.courses[0].id == "778899"


@pytest.mark.parametrize("course_id", ["", "not-numeric", "١٢٣", True, None, -1, 1.5])
def test_invalid_org_unit_id_fails_without_echoing_provider_value(course_id: object) -> None:
    course = dict(OBSERVED_MYCOURSES_BODY["Courses"][0])
    course["OrgUnitId"] = course_id
    client = FakeD2LHttpClient({"Courses": [course], "Bookmark": None})

    with pytest.raises(ProviderEndpointChangedError) as raised:
        BrightspaceCourseProvider(client).list_courses()  # type: ignore[arg-type]

    assert str(raised.value) == "mycourses course object had an invalid OrgUnitId"
    if rendered := str(course_id):
        assert rendered not in str(raised.value)


def test_list_courses_preserves_nonnull_bookmark() -> None:
    body = {**OBSERVED_MYCOURSES_BODY, "Bookmark": "opaque-bookmark-value"}
    client = FakeD2LHttpClient(body)
    page = BrightspaceCourseProvider(client).list_courses()  # type: ignore[arg-type]
    assert page.bookmark == "opaque-bookmark-value"


def test_missing_courses_container_raises_provider_endpoint_changed() -> None:
    client = FakeD2LHttpClient({"NotCourses": []})
    with pytest.raises(ProviderEndpointChangedError):
        BrightspaceCourseProvider(client).list_courses()  # type: ignore[arg-type]


def test_courses_not_a_list_raises_provider_endpoint_changed() -> None:
    client = FakeD2LHttpClient({"Courses": "not-a-list", "Bookmark": None})
    with pytest.raises(ProviderEndpointChangedError):
        BrightspaceCourseProvider(client).list_courses()  # type: ignore[arg-type]


def test_response_not_a_dict_raises_provider_endpoint_changed() -> None:
    client = FakeD2LHttpClient(["unexpected", "list", "response"])
    with pytest.raises(ProviderEndpointChangedError):
        BrightspaceCourseProvider(client).list_courses()  # type: ignore[arg-type]


def test_course_entry_not_an_object_raises_safe_provider_error() -> None:
    client = FakeD2LHttpClient({"Courses": ["PRIVATE COURSE VALUE"], "Bookmark": None})

    with pytest.raises(ProviderEndpointChangedError) as raised:
        BrightspaceCourseProvider(client).list_courses()  # type: ignore[arg-type]

    assert str(raised.value) == "mycourses course entry was not an object"
    assert "PRIVATE" not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Code", None),
        ("Name", 123),
        ("SemesterName", False),
        ("IsActive", "true"),
        ("CanAccessCourse", 1),
        ("IsNewEnrollment", 0),
        ("StartDate", 123),
        ("EndDate", {"private": "value"}),
    ],
)
def test_course_field_type_drift_fails_without_echoing_value(
    field: str, value: object
) -> None:
    course = dict(OBSERVED_MYCOURSES_BODY["Courses"][0])
    course[field] = value
    client = FakeD2LHttpClient({"Courses": [course], "Bookmark": None})

    with pytest.raises(ProviderEndpointChangedError) as raised:
        BrightspaceCourseProvider(client).list_courses()  # type: ignore[arg-type]

    assert str(raised.value) == f"mycourses course object had an invalid {field}"
    assert repr(value) not in str(raised.value)


@pytest.mark.parametrize(
    "missing_field",
    ["OrgUnitId", "Code", "Name", "SemesterName", "IsActive", "CanAccessCourse", "IsNewEnrollment"],
)
def test_missing_required_course_field_raises_provider_endpoint_changed(missing_field: str) -> None:
    course = dict(OBSERVED_MYCOURSES_BODY["Courses"][0])
    del course[missing_field]
    body = {"Courses": [course], "Bookmark": None}
    client = FakeD2LHttpClient(body)
    with pytest.raises(ProviderEndpointChangedError):
        BrightspaceCourseProvider(client).list_courses()  # type: ignore[arg-type]
