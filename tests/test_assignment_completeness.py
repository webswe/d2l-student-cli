"""Assignment-list page-size boundary assessment with fictional HTML."""

from __future__ import annotations

import pytest

from d2l_cli.models.assignment_completeness import AssignmentCompletenessStatus
from d2l_cli.providers.brightspace_assignments import parse_assignment_list_page


def _linked_row(number: int) -> str:
    return (
        f'<tr><td><a href="/submit?db={number}&amp;ou=101">Assignment {number}</a></td>'
        "<td></td><td></td><td></td></tr>"
    )


def _linkless_row() -> str:
    return "<tr><td>Unidentified assignment-like row</td><td></td><td></td><td></td></tr>"


def _page(rows: str, selector: str = "") -> str:
    return f"""
    <form method="post" action="folders_list.d2l?ou=101">
      <d2l-table-wrapper><table>{rows}</table></d2l-table-wrapper>
      {selector}
    </form>
    """


def _selector(size: int) -> str:
    return f"""
    <select aria-label="Items per page">
      <option value="1">1</option>
      <option value="{size}" selected>{size}</option>
    </select>
    """


@pytest.mark.parametrize(
    ("row_count", "ceiling", "expected"),
    [
        (2, 3, AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY),
        (2, 2, AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED),
        (3, 2, AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED),
    ],
)
def test_compares_observed_rows_with_selected_page_size(
    row_count: int,
    ceiling: int,
    expected: AssignmentCompletenessStatus,
) -> None:
    rows = "".join(_linked_row(index + 1) for index in range(row_count))

    result = parse_assignment_list_page(_page(rows, _selector(ceiling)), course_id="101")

    assert result.completeness.status == expected
    assert result.completeness.observed_row_count == row_count
    assert result.completeness.page_size_ceiling == ceiling


def test_missing_page_size_selector_is_unknown() -> None:
    result = parse_assignment_list_page(_page(_linked_row(1)), course_id="101")

    assert result.completeness.status == AssignmentCompletenessStatus.UNKNOWN
    assert result.completeness.observed_row_count == 1
    assert result.completeness.page_size_ceiling is None


def test_form_level_page_size_label_identifies_sole_numeric_selector() -> None:
    selector = """
    <label>Items per page</label>
    <select><option value="10">10</option><option value="50" selected>50</option></select>
    """

    result = parse_assignment_list_page(_page(_linked_row(1), selector), course_id="101")

    assert result.completeness.status == AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY
    assert result.completeness.page_size_ceiling == 50


def test_assignment_title_cannot_supply_page_size_signal() -> None:
    adversarial_row = (
        '<tr><td><a href="/submit?db=1&amp;ou=101">Items per page</a></td>'
        "<td></td><td></td><td></td></tr>"
    )
    unrelated_select = '<select><option value="50" selected>50</option></select>'

    result = parse_assignment_list_page(
        _page(adversarial_row, unrelated_select), course_id="101"
    )

    assert result.completeness.status == AssignmentCompletenessStatus.UNKNOWN
    assert result.completeness.page_size_ceiling is None


def test_ambiguous_selected_page_size_is_unknown() -> None:
    selector = """
    <select aria-label="Items per page">
      <option value="10" selected>10</option>
      <option value="20" selected>20</option>
    </select>
    """

    result = parse_assignment_list_page(_page(_linked_row(1), selector), course_id="101")

    assert result.completeness.status == AssignmentCompletenessStatus.UNKNOWN
    assert result.completeness.page_size_ceiling is None


@pytest.mark.parametrize(
    "selector",
    [
        '<select aria-label="Items per page"><option selected>many</option></select>',
        '<select aria-label="Items per page"><option value="0" selected>0</option></select>',
        '<select aria-label="Items per page"><option value="10" selected>10',
    ],
)
def test_malformed_page_size_selector_is_unknown(selector: str) -> None:
    result = parse_assignment_list_page(_page(_linked_row(1), selector), course_id="101")

    assert result.completeness.status == AssignmentCompletenessStatus.UNKNOWN
    assert result.completeness.page_size_ceiling is None


def test_pathological_page_size_integer_is_unknown_instead_of_raising() -> None:
    pathological = "9" * 10_000
    selector = (
        '<select aria-label="Items per page">'
        f'<option value="{pathological}" selected>{pathological}</option>'
        "</select>"
    )

    result = parse_assignment_list_page(_page(_linked_row(1), selector), course_id="101")

    assert result.completeness.status == AssignmentCompletenessStatus.UNKNOWN
    assert result.completeness.page_size_ceiling is None


def test_empty_result_below_known_boundary_is_not_claimed_complete() -> None:
    empty_message = "<tr><td>No assignments found</td></tr>"

    result = parse_assignment_list_page(
        _page(empty_message, _selector(10)), course_id="101"
    )

    assert result.assignments == ()
    assert result.completeness.status == AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY
    assert result.completeness.observed_row_count == 0


def test_linkless_assignment_shaped_row_counts_before_normalization() -> None:
    result = parse_assignment_list_page(
        _page(_linked_row(1) + _linkless_row(), _selector(2)),
        course_id="101",
    )

    assert [assignment.id for assignment in result.assignments] == ["1"]
    assert result.completeness.observed_row_count == 2
    assert result.completeness.status == AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED


def test_duplicate_and_course_mismatched_rows_still_count() -> None:
    rows = (
        _linked_row(1)
        + _linked_row(1)
        + '<tr><td><a href="/submit?db=2&amp;ou=999">Other course</a></td>'
        "<td></td><td></td><td></td></tr>"
    )

    result = parse_assignment_list_page(_page(rows, _selector(3)), course_id="101")

    assert [assignment.id for assignment in result.assignments] == ["1"]
    assert result.completeness.observed_row_count == 3
    assert result.completeness.status == AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED


def test_unexpected_multi_cell_linkless_row_is_counted_conservatively() -> None:
    unexpected = "<tr><td>Unexpected</td><td>shape</td></tr>"

    result = parse_assignment_list_page(
        _page(_linked_row(1) + unexpected, _selector(10)), course_id="101"
    )

    assert result.completeness.status == AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY
    assert result.completeness.observed_row_count == 2
    assert result.completeness.page_size_ceiling == 10
