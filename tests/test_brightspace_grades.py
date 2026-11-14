from __future__ import annotations

from collections.abc import Callable, Mapping

import httpx
import pytest
import respx

from d2l_cli.client.errors import (
    NotAuthenticatedError,
    PermissionDeniedError,
    ProviderEndpointChangedError,
)
from d2l_cli.client.http import D2LHttpClient, D2LTextResponse
from d2l_cli.models.grade import FeedbackAvailability
from d2l_cli.providers.brightspace_grades import (
    BrightspaceGradeProvider,
    parse_grade_list_html,
)

_BASE = "https://example.brightspace.com"
_INDEX_PATH = "/d2l/lms/grades/index.d2l"
_MAIN_PATH = "/d2l/lms/grades/my_grades/main.d2l"
_COURSE_ID = "620000"

_FLAT_HTML = """
<d2l-table-wrapper><table>
  <tr><th>Grade Item</th><th>Points</th><th>Grade</th><th>Feedback</th></tr>
  <tr><th>Fictional Quiz Alpha</th><td>7 / 9</td><td>77.8%</td><td></td></tr>
  <tr><th>Fictional Essay Beta</th><td>Pending Review</td><td></td>
      <td>Feedback is available</td></tr>
</table></d2l-table-wrapper>
"""

_WEIGHTED_HTML = """
<d2l-table-wrapper><table>
  <tr><th colspan="2">Grade Item</th><th>Points</th><th>Weight</th>
      <th>Grade</th><th>Feedback</th></tr>
  <tr><th colspan="2">Fictional Group</th><td></td><td>4 / 5</td>
      <td>80%</td><td></td></tr>
  <tr><td><d2l-indent></d2l-indent></td><th>Fictional Lab One</th><td>8 / 10</td><td>2 / 3</td>
      <td>80%</td><td><a href="/fictional">View feedback</a></td></tr>
  <tr><td></td><th>Fictional Lab Two</th><td>Not Yet Available</td>
      <td>Not Yet Available</td><td>Not Yet Available</td>
      <td><d2l-feedback-indicator></d2l-feedback-indicator></td></tr>
</table></d2l-table-wrapper>
"""


def test_parser_preserves_flat_item_order_and_display_values() -> None:
    page = parse_grade_list_html(_FLAT_HTML, course_id=_COURSE_ID)

    assert [item.label for item in page.items] == [
        "Fictional Quiz Alpha",
        "Fictional Essay Beta",
    ]
    first, second = page.items
    assert first.course_id == _COURSE_ID
    assert first.displayed_points == "7 / 9"
    assert first.displayed_grade == "77.8%"
    assert first.displayed_weight is None
    assert first.feedback_availability is FeedbackAvailability.NONE
    assert second.displayed_points == "Pending Review"
    assert second.displayed_grade is None
    assert second.feedback_availability is FeedbackAvailability.PRESENT
    assert page.category_row_count == 0
    assert page.header_effective_width == 4
    assert page.has_weight_column is False


def test_parser_excludes_categories_and_supports_indented_weighted_items() -> None:
    page = parse_grade_list_html(_WEIGHTED_HTML, course_id=_COURSE_ID)

    assert page.category_row_count == 1
    assert [item.label for item in page.items] == [
        "Fictional Lab One",
        "Fictional Lab Two",
    ]
    assert all(item.label != "Fictional Group" for item in page.items)
    assert page.header_effective_width == 6
    assert page.has_weight_column is True
    assert page.items[0].displayed_weight == "2 / 3"
    assert page.items[0].feedback_availability is FeedbackAvailability.PRESENT
    assert page.items[1].displayed_points == "Not Yet Available"
    assert page.items[1].displayed_grade == "Not Yet Available"
    assert page.items[1].displayed_weight == "Not Yet Available"
    assert page.items[1].feedback_availability is FeedbackAvailability.UNKNOWN


def test_parser_allows_a_genuinely_empty_supported_table() -> None:
    html = """
    <d2l-table-wrapper><table>
      <tr><th>Grade Item</th><th>Points</th><th>Grade</th><th>Feedback</th></tr>
    </table></d2l-table-wrapper>
    """
    assert parse_grade_list_html(html, course_id=_COURSE_ID).items == ()


@pytest.mark.parametrize(
    ("html", "reason"),
    [
        (
            "<d2l-table-wrapper><table><tr><th>Grade Item</th><th>Points</th>",
            "malformed HTML",
        ),
        (
            """
            <d2l-table-wrapper><table>
              <tr><th>Grade Item</th><th>Points</th><th>Grade</th><th>Feedback</th></tr>
              <tr><th>Fictional Item</th><td>4 / 5</td><td>80%</td></tr>
            </table></d2l-table-wrapper>
            """,
            "row width",
        ),
        (
            """
            <d2l-table-wrapper><table>
              <tr><th>Grade Item</th><th>Points</th><th>Grade</th><th>Feedback</th></tr>
              <tr><th rowspan="2">Fictional Item</th><td>4 / 5</td><td>80%</td><td></td></tr>
            </table></d2l-table-wrapper>
            """,
            "body span",
        ),
        (
            """
            <d2l-table-wrapper><table>
              <tr><th colspan="3">Grade Item</th><th>Points</th><th>Grade</th><th>Feedback</th></tr>
            </table></d2l-table-wrapper>
            """,
            "grade-item header colspan",
        ),
        (
            """
            <d2l-table-wrapper><table>
              <tr><th>Grade Item</th><th>Points</th><th>Grade</th><th>Feedback</th></tr>
              <tr><td>Fictional Item</td><td>4 / 5</td><td>80%</td><td></td></tr>
            </table></d2l-table-wrapper>
            """,
            "row geometry",
        ),
        (
            """
            <d2l-table-wrapper><table>
              <tr><th>Grade Item</th><th>Points</th><th>Grade</th><th>Feedback</th></tr>
              <tr><th>Overall Grade</th><td>4 / 5</td><td>80%</td><td></td></tr>
            </table></d2l-table-wrapper>
            """,
            "summary row",
        ),
    ],
)
def test_parser_rejects_unsupported_structure_without_partial_records(
    html: str, reason: str
) -> None:
    with pytest.raises(ProviderEndpointChangedError, match=reason):
        parse_grade_list_html(html, course_id=_COURSE_ID)


def test_parser_error_never_contains_private_academic_text() -> None:
    private_label = "PRIVATE FICTIONAL ITEM 998877"
    html = f"""
    <d2l-table-wrapper><table>
      <tr><th>Grade Item</th><th>Points</th><th>Grade</th><th>Feedback</th></tr>
      <tr><td>{private_label}</td><td>SECRET GRADE 665544</td><td></td><td></td></tr>
    </table></d2l-table-wrapper>
    """
    with pytest.raises(ProviderEndpointChangedError) as exc_info:
        parse_grade_list_html(html, course_id=_COURSE_ID)
    assert private_label not in str(exc_info.value)
    assert "SECRET GRADE" not in str(exc_info.value)


def test_nested_table_content_does_not_change_outer_cell_alignment() -> None:
    html = """
    <d2l-table-wrapper><table>
      <tr><th>Grade Item</th><th>Points</th><th>Grade</th><th>Feedback</th></tr>
      <tr><th>Fictional Item<table><tr><td>Nested layout text</td></tr></table></th>
          <td>4 / 5</td><td>80%</td><td></td></tr>
    </table></d2l-table-wrapper>
    """

    page = parse_grade_list_html(html, course_id=_COURSE_ID)
    assert page.items[0].label == "Fictional Item"


@pytest.mark.parametrize("bad_course_id", ["", "abc", "12a", "-1", "１２３"])
def test_parser_rejects_invalid_course_context(bad_course_id: str) -> None:
    with pytest.raises(ValueError, match="course_id"):
        parse_grade_list_html(_FLAT_HTML, course_id=bad_course_id)


class _FakeRedirectClient:
    def __init__(
        self,
        *,
        location: str = f"{_MAIN_PATH}?ou={_COURSE_ID}",
        html: str = _FLAT_HTML,
        raises: Exception | None = None,
    ) -> None:
        self.location = location
        self.html = html
        self.raises = raises
        self.calls: list[tuple[str, Mapping[str, str] | None, str]] = []
        self.validated_target: tuple[str, Mapping[str, str] | None] | None = None

    def get_text_following_one_redirect(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None,
        validate_redirect: Callable[
            [str], tuple[str, Mapping[str, str] | None]
        ],
        expected_content_type: str = "text/html",
    ) -> D2LTextResponse:
        self.calls.append((path, params, expected_content_type))
        if self.raises is not None:
            raise self.raises
        self.validated_target = validate_redirect(self.location)
        return D2LTextResponse(200, self.html, "text/html")


def test_provider_uses_established_redirect_path_and_course_context() -> None:
    client = _FakeRedirectClient()
    result = BrightspaceGradeProvider(client).get_grade_list(_COURSE_ID)  # type: ignore[arg-type]

    assert len(result.items) == 2
    assert client.calls == [(_INDEX_PATH, {"ou": _COURSE_ID}, "text/html")]
    assert client.validated_target == (_MAIN_PATH, {"ou": _COURSE_ID})


@pytest.mark.parametrize(
    "location",
    [
        f"{_MAIN_PATH}?ou=999999",
        f"{_MAIN_PATH}?ou={_COURSE_ID}&extra=1",
        f"https://example.brightspace.com{_MAIN_PATH}?ou={_COURSE_ID}",
        f"https://foreign.invalid{_MAIN_PATH}?ou={_COURSE_ID}",
        f"/d2l/other/path?ou={_COURSE_ID}",
    ],
)
def test_provider_rejects_mismatched_or_unexpected_redirect(location: str) -> None:
    client = _FakeRedirectClient(location=location)
    with pytest.raises(ProviderEndpointChangedError, match="unexpected redirect"):
        BrightspaceGradeProvider(client).list_grade_items(_COURSE_ID)  # type: ignore[arg-type]
    assert client.validated_target is None


def test_provider_rejects_bad_course_id_before_request() -> None:
    client = _FakeRedirectClient()
    with pytest.raises(ValueError, match="course_id"):
        BrightspaceGradeProvider(client).list_grade_items("not-numeric")  # type: ignore[arg-type]
    assert client.calls == []


def test_provider_propagates_transport_failure() -> None:
    client = _FakeRedirectClient(raises=PermissionDeniedError("denied"))
    with pytest.raises(PermissionDeniedError):
        BrightspaceGradeProvider(client).list_grade_items(_COURSE_ID)  # type: ignore[arg-type]


@respx.mock
def test_real_provider_rejects_absolute_redirect_without_following_it() -> None:
    index = respx.get(f"{_BASE}{_INDEX_PATH}").mock(
        return_value=httpx.Response(
            302,
            headers={"location": f"{_BASE}{_MAIN_PATH}?ou={_COURSE_ID}"},
        )
    )
    final = respx.get(f"{_BASE}{_MAIN_PATH}").mock(
        return_value=httpx.Response(
            200, text=_FLAT_HTML, headers={"content-type": "text/html"}
        )
    )
    with D2LHttpClient(_BASE) as client:
        with pytest.raises(ProviderEndpointChangedError, match="unexpected redirect"):
            BrightspaceGradeProvider(client).list_grade_items(_COURSE_ID)
    assert index.called
    assert not final.called


@respx.mock
def test_real_provider_propagates_expired_authentication() -> None:
    respx.get(f"{_BASE}{_INDEX_PATH}").mock(
        return_value=httpx.Response(
            302, headers={"location": "/d2l/login?sessionExpired=1"}
        )
    )
    with D2LHttpClient(_BASE) as client:
        with pytest.raises(NotAuthenticatedError):
            BrightspaceGradeProvider(client).list_grade_items(_COURSE_ID)
