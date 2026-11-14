"""Phase 3B: clean assignment titles and optional due dates.

Fixtures are synthetic but mirror the STRUCTURE observed in the Phase 3A
recon and a value-free structural inspection of the live page (see
docs/PROVIDER_NOTES.md, "Phase 3A" / "Phase 3B"): a ``th scope=row`` name cell with a
``d2l-foldername-medium-font`` title container (title either an identity
link's ``strong`` or a plain ``label > strong``), and a dates wrapper holding
the due date (``label > strong``, "Due on <date>") and availability text
(``ul > li``). No real LMS value appears anywhere in this file.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.providers.brightspace_assignments import parse_assignment_list_html

COURSE = "620000"
_QUERY = "db={db}&amp;grpid=0&amp;isprv=0&amp;ou=620000"
_ID_LINK = "/d2l/lms/dropbox/user/folder_submit_files.d2l?" + _QUERY
_HISTORY_LINK = "/d2l/lms/dropbox/user/folders_history.d2l?" + _QUERY

AVAILABILITY = (
    '<div class="dco d2l-folderdate-wrapper-row"><div class="dco_c">'
    '<d2l-tooltip-help text="x"><ul class="dl dl_di">'
    "<li>Available on Aug 1, 2026 12:00 AM"
    '<ul class="dl dl_no"><li><strong>Availability ends Dec 1, 2026 11:59 PM</strong></li></ul>'
    "</li></ul></d2l-tooltip-help></div></div>"
)


def due_block(text: str = "Due on Sep 5, 2026 9:05 AM") -> str:
    return (
        '<div class="dco d2l-dates-text"><div class="dco_c">'
        f"<label><strong>{text}</strong></label></div></div>"
    )


def linked_title(title: str, db: str = "90210") -> str:
    href = _ID_LINK.format(db=db)
    return f'<a class="d2l-link d2l-link-inline" href="{href}"><strong>{title}</strong></a>'


def label_title(title: str) -> str:
    return f"<label><strong>{title}</strong></label>"


def name_cell(title_html: str, dates_html: str = "") -> str:
    return (
        '<th scope="row" class="d_ich">'
        '<div class="dco d2l-foldername"><div class="dco_c">'
        '<div class="dco d2l-foldername-medium-font"><div class="dco_c">'
        f"{title_html}</div></div></div></div>"
        '<div class="clear"></div>'
        f'<div class="dco d2l-folderdates-wrapper"><div class="dco_c">{dates_html}</div></div>'
        "</th>"
    )


def row(
    title_html: str,
    dates_html: str = "",
    *,
    status: str = "Submitted",
    score: str = "17 / 20",
    status_link_db: str | None = None,
) -> str:
    if status_link_db:
        href = _HISTORY_LINK.format(db=status_link_db)
        status_cell = f'<td><a class="d2l-link" href="{href}">{status}</a></td>'
    else:
        status_cell = f"<td>{status}</td>"
    return f"<tr>{name_cell(title_html, dates_html)}{status_cell}<td>{score}</td><td></td></tr>"


def page(*rows: str) -> str:
    return (
        "<html><body><d2l-table-wrapper><table>"
        "<tr><th>Assignment</th><th>Status</th><th>Score</th><th>Feedback</th></tr>"
        '<tr><td colspan="4">Group divider</td></tr>'
        f"{''.join(rows)}</table></d2l-table-wrapper></body></html>"
    )


def parse(*rows: str) -> tuple:  # type: ignore[type-arg]
    return parse_assignment_list_html(page(*rows), course_id=COURSE)


SEP5 = datetime(2026, 9, 5, 9, 5)


# --- clean titles (matrix 1-10) ---------------------------------------------


def test_title_only_row() -> None:
    [a] = parse(row(linked_title("Homework 1")))
    assert a.name == "Homework 1"
    assert a.due_date is None


def test_title_plus_due_structure() -> None:
    [a] = parse(row(linked_title("Homework 1"), due_block()))
    assert a.name == "Homework 1"
    assert a.due_date == SEP5


def test_title_plus_availability_structure() -> None:
    [a] = parse(row(linked_title("Homework 1"), AVAILABILITY))
    assert a.name == "Homework 1"
    assert a.due_date is None


def test_title_plus_due_plus_availability() -> None:
    [a] = parse(row(linked_title("Homework 1"), due_block() + AVAILABILITY))
    assert a.name == "Homework 1"
    assert a.due_date == SEP5


def test_title_containing_numbers() -> None:
    [a] = parse(row(linked_title("Lab 12: Problems 3-9 (2 parts)"), due_block()))
    assert a.name == "Lab 12: Problems 3-9 (2 parts)"


def test_legitimate_title_containing_a_month_name() -> None:
    [a] = parse(row(linked_title("March Madness Bracket"), due_block()))
    assert a.name == "March Madness Bracket"
    assert a.due_date == SEP5


def test_legitimate_title_containing_the_word_due() -> None:
    [a] = parse(row(linked_title("Due Diligence Report"), due_block()))
    assert a.name == "Due Diligence Report"
    assert a.due_date == SEP5


def test_title_containing_date_like_text_is_kept_verbatim() -> None:
    [a] = parse(row(linked_title("Essay for Oct 3, 2026 9:00 AM Session"), due_block()))
    assert a.name == "Essay for Oct 3, 2026 9:00 AM Session"
    assert a.due_date == SEP5  # comes from the due structure, not the title


def test_metadata_descendants_outside_title_are_excluded() -> None:
    extra = (
        '<div class="dco"><div class="dco_c"><span>NEW</span></div></div><div>Extra metadata</div>'
    )
    html = row(linked_title("Homework 1"), extra + due_block() + AVAILABILITY)
    [a] = parse(html)
    assert a.name == "Homework 1"


def test_whitespace_and_nested_title_markup() -> None:
    title = '<a class="d2l-link" href="{}">\n   <strong>  Home<em>work</em>\n  1 </strong></a>'
    [a] = parse(row(title.format(_ID_LINK.format(db="90210")), due_block()))
    assert a.name == "Homework 1"


def test_plain_label_title_without_identity_link_in_name_cell() -> None:
    # Observed for dated rows: the title is a plain label>strong and the
    # identity link lives in the status cell.
    [a] = parse(row(label_title("Homework 2"), due_block(), status_link_db="90211"))
    assert a.id == "90211"
    assert a.name == "Homework 2"
    assert a.due_date == SEP5


def test_title_container_without_text_gives_empty_name_not_metadata() -> None:
    [a] = parse(row(linked_title(""), due_block()))
    assert a.name == ""
    assert a.due_date == SEP5


def test_title_never_falls_back_to_joining_the_whole_cell() -> None:
    [a] = parse(row(label_title("Homework 2"), due_block() + AVAILABILITY, status_link_db="1"))
    for fragment in ("Due on", "Available", "Sep", "2026", "Availability"):
        assert fragment not in a.name


# --- fallbacks for cells without the title container ------------------------


def test_cell_without_container_uses_first_identity_link() -> None:
    html = (
        "<d2l-table-wrapper><table><tr>"
        '<td><a href="/x?db=5">Home<em>work</em> 5</a> <span>Due on Sep 5, 2026 9:05 AM</span></td>'
        "<td>Submitted</td><td></td><td></td></tr></table></d2l-table-wrapper>"
    )
    [a] = parse_assignment_list_html(html, course_id="1")
    assert a.name == "Homework 5"
    assert a.due_date is None  # not the proven label>strong structure


def test_cell_without_container_or_link_uses_first_text_node_only() -> None:
    html = (
        "<d2l-table-wrapper><table><tr>"
        "<td>Homework 6<div>metadata text</div></td>"
        '<td><a href="/x?db=6">Submitted</a></td><td></td><td></td></tr>'
        "</table></d2l-table-wrapper>"
    )
    [a] = parse_assignment_list_html(html, course_id="1")
    assert a.name == "Homework 6"


# --- due dates (matrix 11-22) -----------------------------------------------


def test_assignment_with_due_date() -> None:
    [a] = parse(row(linked_title("A"), due_block("Due on Dec 31, 2026 11:59 PM")))
    assert a.due_date == datetime(2026, 12, 31, 23, 59)


def test_assignment_without_due_date_is_valid_not_an_error() -> None:
    assignments = parse(row(linked_title("A")), row(linked_title("B", "90211")))
    assert [a.due_date for a in assignments] == [None, None]


def test_availability_date_only_is_never_a_due_date() -> None:
    [a] = parse(row(linked_title("A"), AVAILABILITY))
    assert a.due_date is None


def test_due_plus_availability_uses_only_due() -> None:
    [a] = parse(row(linked_title("A"), AVAILABILITY + due_block() + AVAILABILITY))
    assert a.due_date == SEP5


def test_random_date_elsewhere_in_name_cell_is_not_due() -> None:
    misc = "<div><span>Sep 5, 2026 9:05 AM</span></div><p>Due on Sep 5, 2026 9:05 AM</p>"
    [a] = parse(row(linked_title("A"), misc))
    assert a.due_date is None  # wrong structure (no label>strong)


def test_due_text_in_wrong_element_shape_is_not_due() -> None:
    for wrapper in (
        "<span>{}</span>",
        "<label>{}</label>",
        "<strong>{}</strong>",
        "<strong><label>{}</label></strong>",
        "<div><strong>{}</strong></div>",
    ):
        [a] = parse(row(linked_title("A"), wrapper.format("Due on Sep 5, 2026 9:05 AM")))
        assert a.due_date is None, wrapper


def test_date_in_status_column_is_not_due() -> None:
    [a] = parse(row(linked_title("A"), status="Submitted Sep 5, 2026 9:05 AM"))
    assert a.due_date is None
    assert a.submission_status == "Submitted Sep 5, 2026 9:05 AM"  # existing behavior kept


def test_date_in_score_or_action_column_is_not_due() -> None:
    html = row(linked_title("A"), score="Due on Sep 5, 2026 9:05 AM")
    assert parse(html)[0].due_date is None


def test_malformed_due_structure_is_absent_not_an_error() -> None:
    for text in (
        "Due on",
        "Due on Sept 5",
        "Due on Sep 5, 2026",  # no time: not a proven format
        "Due on 9/5/2026 9:05 AM",  # different format: not proven
        "Due on Sep 5, 2026 25:05 AM",
        "Due on Sep 5, 2026 0:05 AM",  # 12-hour clock has no hour 0
        "Due on Sep 5, 2026 9:75 AM",
        "Due on Feb 30, 2026 9:05 AM",  # impossible date
        "Due on Sep 5, 2026 9:05 AM extra trailing text",
        "Overdue Sep 5, 2026 9:05 AM",
    ):
        [a] = parse(row(linked_title("A"), due_block(text)))
        assert a.due_date is None, text


def test_due_marker_without_parseable_date_is_absent() -> None:
    [a] = parse(row(linked_title("A"), due_block("Due on soon")))
    assert a.due_date is None


def test_parseable_date_without_due_marker_is_absent() -> None:
    for text in ("Sep 5, 2026 9:05 AM", "Starts Sep 5, 2026 9:05 AM", "Due Sep 5, 2026 9:05 AM"):
        [a] = parse(row(linked_title("A"), due_block(text)))
        assert a.due_date is None, text


def test_multiple_availability_dates_do_not_affect_due() -> None:
    [a] = parse(row(linked_title("A"), AVAILABILITY + AVAILABILITY + due_block()))
    assert a.due_date == SEP5


def test_two_due_markers_are_ambiguous_and_yield_no_due_date() -> None:
    html = row(linked_title("A"), due_block() + due_block("Due on Oct 1, 2026 9:05 AM"))
    assert parse(html)[0].due_date is None


def test_assignment_title_that_is_itself_a_parseable_due_string() -> None:
    [a] = parse(row(label_title("Due on Sep 5, 2026 9:05 AM"), "", status_link_db="9"))
    assert a.name == "Due on Sep 5, 2026 9:05 AM"
    assert a.due_date is None  # the title node is never a due candidate


def test_title_link_text_that_looks_like_due_marker_in_label_strong_is_excluded() -> None:
    title = '<a href="/x?db=3"><label><strong>Due on Sep 5, 2026 9:05 AM</strong></label></a>'
    [a] = parse_assignment_list_html(
        f"<d2l-table-wrapper><table><tr><td>{title}</td><td></td><td></td><td></td></tr>"
        "</table></d2l-table-wrapper>",
        course_id="1",
    )
    assert a.due_date is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Due on Jan 1, 2027 12:00 AM", datetime(2027, 1, 1, 0, 0)),
        ("Due on Jan 1, 2027 12:30 PM", datetime(2027, 1, 1, 12, 30)),
        ("Due on Mar 9, 2026 1:05 pm", datetime(2026, 3, 9, 13, 5)),
        ("due on OCT 15, 2026 11:59 PM", datetime(2026, 10, 15, 23, 59)),
        ("Due on Feb 28, 2028 6:00 AM", datetime(2028, 2, 28, 6, 0)),
        ("Due on Feb 29, 2028 6:00 AM", datetime(2028, 2, 29, 6, 0)),
    ],
)
def test_supported_due_format_is_parsed_deterministically(text: str, expected: datetime) -> None:
    [a] = parse(row(linked_title("A"), due_block(text)))
    assert a.due_date == expected
    assert a.due_date is not None and a.due_date.tzinfo is None  # no timezone asserted


def test_due_date_parsing_does_not_depend_on_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    import locale

    monkeypatch.setattr(locale, "setlocale", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    [a] = parse(row(linked_title("A"), due_block("Due on Sep 5, 2026 9:05 AM")))
    assert a.due_date == SEP5


# --- rows without an assignment identity link (matrix 23-25) ----------------


def _linkless_row(title: str, dates: str) -> str:
    return row(label_title(title), dates)  # status cell has no link either


def test_dated_four_cell_row_with_no_identity_link_is_ignored() -> None:
    assert parse(_linkless_row("Homework 9", due_block())) == ()


def test_linkless_row_with_due_and_availability_is_ignored() -> None:
    assert parse(_linkless_row("Homework 9", due_block() + AVAILABILITY)) == ()


def test_linkless_assignment_like_row_is_ignored_and_neighbours_survive() -> None:
    linked = row(linked_title("Real one", "90210"), due_block())
    assignments = parse(_linkless_row("Looks like one", due_block()), linked)
    assert [a.id for a in assignments] == ["90210"]
    assert assignments[0].name == "Real one"


def test_due_date_is_never_used_as_identity() -> None:
    html = page(row(label_title("X"), due_block("Due on Sep 5, 2026 9:05 AM")))
    assert parse_assignment_list_html(html, course_id=COURSE) == ()


# --- preserved behavior -----------------------------------------------------


def test_status_and_score_are_unchanged_by_due_extraction() -> None:
    [a] = parse(row(linked_title("A"), due_block(), status="Submitted", score="18 / 20"))
    assert (a.submission_status, a.score) == ("Submitted", "18 / 20")


def test_duplicate_identity_still_keeps_first_row_only() -> None:
    assignments = parse(
        row(linked_title("First", "1"), due_block()), row(linked_title("Second", "1"))
    )
    assert [a.name for a in assignments] == ["First"]


def test_course_mismatch_still_drops_the_row() -> None:
    other = row(linked_title("A").replace("ou=620000", "ou=999999"))
    assert parse(other) == ()


def test_page_without_wrapper_is_still_endpoint_changed() -> None:
    with pytest.raises(ProviderEndpointChangedError):
        parse_assignment_list_html("<html><body><p>hi</p></body></html>", course_id=COURSE)
