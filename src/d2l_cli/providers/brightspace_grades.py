"""GET-only provider and strict parser for visible Brightspace grade items.

The contract comes from ``docs/PROVIDER_NOTES.md`` Phase 5A.1. Production parsing is
header-derived and fail-closed: the verified flat and indented item shapes are
accepted, spanning group/category rows are counted but excluded, and any other
row geometry raises ``ProviderEndpointChangedError``. Displayed values remain
strings; this module performs no academic calculations or state inference.

The provider starts at the observed Grades index route and uses the shared
HTTP client's one-redirect GET path. Its validator accepts only the exact
relative final route with one course-matching ``ou`` query parameter. Raw HTML,
redirect locations, labels, and grade values never enter exceptions or logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.models.grade import FeedbackAvailability, GradeItem, GradeListPage

_GRADES_INDEX_PATH = "/d2l/lms/grades/index.d2l"
_GRADES_MAIN_PATH = "/d2l/lms/grades/my_grades/main.d2l"
_MAX_PARSED_SPAN = 32

_HEADER_ALIASES = (
    ("grade_item", frozenset({"grade item", "item", "name"})),
    ("feedback", frozenset({"feedback", "comments", "comment"})),
    ("weight", frozenset({"weight", "weighted"})),
    ("points", frozenset({"points"})),
    ("grade", frozenset({"grade"})),
)
_REQUIRED_HEADERS = frozenset({"grade_item", "points", "grade", "feedback"})
_HEADER_ORDER_WITHOUT_WEIGHT = ("grade_item", "points", "grade", "feedback")
_HEADER_ORDER_WITH_WEIGHT = (
    "grade_item",
    "points",
    "weight",
    "grade",
    "feedback",
)
_SUMMARY_LABELS = frozenset(
    {
        "final calculated grade",
        "calculated final grade",
        "final adjusted grade",
        "adjusted final grade",
        "overall grade",
    }
)
_MARKER_TAGS = {
    "a": "link",
    "button": "button",
    "d2l-icon": "icon",
    "img": "image",
}


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _header_semantic(text: str) -> str | None:
    normalized = _normalize_text(text).lower()
    padded = f" {normalized} "
    for semantic, aliases in _HEADER_ALIASES:
        if any(f" {alias} " in padded for alias in aliases):
            return semantic
    return None


def _is_course_id(value: str) -> bool:
    return bool(value) and value.isascii() and value.isdecimal()


def _parse_span(value: str | None, *, duplicated: bool) -> tuple[int, bool]:
    if duplicated:
        return 1, False
    if value is None:
        return 1, True
    if len(value) > 2 or not value.isascii() or not value.isdecimal():
        return 1, False
    span = int(value)
    return (span, True) if 1 <= span <= _MAX_PARSED_SPAN else (1, False)


def _attribute(attrs: list[tuple[str, str | None]], name: str) -> tuple[str | None, bool]:
    values = [value for key, value in attrs if key.lower() == name]
    return (values[0], len(values) > 1) if values else (None, False)


def _structure_error(detail: str) -> ProviderEndpointChangedError:
    return ProviderEndpointChangedError(
        f"Grades page has unsupported table structure ({detail}); refusing "
        "to return potentially misaligned grade data"
    )


@dataclass(slots=True)
class _Cell:
    tag: str
    colspan: int
    rowspan: int
    spans_valid: bool
    text_chunks: list[str] = field(default_factory=list)
    markers: set[str] = field(default_factory=set)

    @property
    def text(self) -> str:
        return _normalize_text("".join(self.text_chunks))


@dataclass(slots=True)
class _Table:
    rows: list[list[_Cell]] = field(default_factory=list)
    current_row: list[_Cell] | None = None
    current_cell: _Cell | None = None


class _GradeHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[_Cell]]] = []
        self.saw_wrapper = False
        self.wrapper_count = 0
        self.malformed = False
        self._wrapper_depth = 0
        self._table_depth = 0
        self._table: _Table | None = None

    def _finish_cell(self) -> None:
        if self._table is None or self._table.current_cell is None:
            return
        if self._table.current_row is None:
            self.malformed = True
        else:
            self._table.current_row.append(self._table.current_cell)
        self._table.current_cell = None

    def _finish_row(self) -> None:
        if self._table is None or self._table.current_row is None:
            return
        self._finish_cell()
        self._table.rows.append(self._table.current_row)
        self._table.current_row = None

    def _finish_table(self) -> None:
        if self._table is None:
            self.malformed = True
            return
        self._finish_row()
        self.tables.append(self._table.rows)
        self._table = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "d2l-table-wrapper":
            self.saw_wrapper = True
            self.wrapper_count += 1
            self._wrapper_depth += 1
            return
        if tag == "table" and self._wrapper_depth:
            if self._table_depth == 0:
                self._table = _Table()
            self._table_depth += 1
            return
        if self._table_depth != 1 or self._table is None:
            return
        if tag == "tr":
            if self._table.current_row is not None:
                self._finish_row()
            self._table.current_row = []
            return
        if tag in {"th", "td"} and self._table.current_row is not None:
            self._finish_cell()
            colspan_value, duplicate_colspan = _attribute(attrs, "colspan")
            rowspan_value, duplicate_rowspan = _attribute(attrs, "rowspan")
            colspan, colspan_valid = _parse_span(colspan_value, duplicated=duplicate_colspan)
            rowspan, rowspan_valid = _parse_span(rowspan_value, duplicated=duplicate_rowspan)
            self._table.current_cell = _Cell(
                tag=tag,
                colspan=colspan,
                rowspan=rowspan,
                spans_valid=colspan_valid and rowspan_valid,
            )
            return
        cell = self._table.current_cell
        if cell is not None:
            marker = _MARKER_TAGS.get(tag)
            if marker is not None:
                cell.markers.add(marker)
            elif "-" in tag:
                cell.markers.add("custom_element")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._table_depth == 1:
            self._finish_cell()
        elif tag == "tr" and self._table_depth == 1:
            self._finish_row()
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._finish_table()
        elif tag == "d2l-table-wrapper" and self._wrapper_depth:
            if self._table_depth:
                self.malformed = True
            self._wrapper_depth -= 1

    def handle_data(self, data: str) -> None:
        if (
            self._table_depth == 1
            and self._table is not None
            and self._table.current_cell is not None
        ):
            self._table.current_cell.text_chunks.append(data)

    def finish(self) -> None:
        if self._table_depth or self._table is not None or self._wrapper_depth:
            self.malformed = True


@dataclass(frozen=True, slots=True)
class _HeaderLayout:
    semantics: tuple[str, ...]
    effective_width: int
    grade_item_span: int

    @property
    def has_weight(self) -> bool:
        return "weight" in self.semantics


def _validated_header(rows: list[list[_Cell]]) -> _HeaderLayout:
    candidates: list[tuple[int, list[_Cell], tuple[str | None, ...]]] = []
    for index, row in enumerate(rows):
        if not row or any(cell.tag != "th" for cell in row):
            continue
        semantics = tuple(_header_semantic(cell.text) for cell in row)
        if _REQUIRED_HEADERS.issubset({value for value in semantics if value}):
            candidates.append((index, row, semantics))
    if len(candidates) != 1:
        raise _structure_error("missing or ambiguous header")

    index, row, optional_semantics = candidates[0]
    if index != 0 or any(semantic is None for semantic in optional_semantics):
        raise _structure_error("unrecognized header")
    semantics = tuple(semantic for semantic in optional_semantics if semantic is not None)
    if semantics not in {_HEADER_ORDER_WITHOUT_WEIGHT, _HEADER_ORDER_WITH_WEIGHT}:
        raise _structure_error("unexpected header order")
    if len(set(semantics)) != len(semantics):
        raise _structure_error("duplicate header")
    if any(not cell.spans_valid or cell.rowspan != 1 for cell in row):
        raise _structure_error("header span")

    grade_item_span = row[0].colspan
    if grade_item_span not in {1, 2}:
        raise _structure_error("grade-item header colspan")
    if any(cell.colspan != 1 for cell in row[1:]):
        raise _structure_error("value header colspan")
    return _HeaderLayout(
        semantics=semantics,
        effective_width=sum(cell.colspan for cell in row),
        grade_item_span=grade_item_span,
    )


def _feedback_availability(cell: _Cell) -> FeedbackAvailability:
    if cell.text:
        return FeedbackAvailability.PRESENT
    if cell.markers:
        return FeedbackAvailability.UNKNOWN
    return FeedbackAvailability.NONE


def _value_by_semantic(cells: list[_Cell], layout: _HeaderLayout) -> dict[str, _Cell]:
    label_offset = 0 if layout.grade_item_span == 1 else 1
    values: dict[str, _Cell] = {"grade_item": cells[label_offset]}
    physical_index = layout.grade_item_span
    for semantic in layout.semantics[1:]:
        values[semantic] = cells[physical_index]
        physical_index += 1
    return values


def _is_category_row(row: list[_Cell], layout: _HeaderLayout) -> bool:
    return (
        layout.grade_item_span == 2
        and len(row) == len(layout.semantics)
        and row[0].tag == "th"
        and row[0].colspan == 2
        and bool(row[0].text)
        and all(cell.tag == "td" and cell.colspan == 1 for cell in row[1:])
    )


def _is_item_row(row: list[_Cell], layout: _HeaderLayout) -> bool:
    if layout.grade_item_span == 1:
        return (
            len(row) == len(layout.semantics)
            and row[0].tag == "th"
            and row[0].colspan == 1
            and bool(row[0].text)
            and all(cell.tag == "td" and cell.colspan == 1 for cell in row[1:])
        )
    return (
        len(row) == len(layout.semantics) + 1
        and row[0].tag == "td"
        and row[0].colspan == 1
        and not row[0].text
        and row[1].tag == "th"
        and row[1].colspan == 1
        and bool(row[1].text)
        and all(cell.tag == "td" and cell.colspan == 1 for cell in row[2:])
    )


def parse_grade_list_html(html: str, *, course_id: str) -> GradeListPage:
    """Parse one verified Grades table or fail without returning partial data."""
    if not _is_course_id(course_id):
        raise ValueError("course_id must be a non-empty numeric Brightspace org-unit id")
    parser = _GradeHTMLParser()
    parser.feed(html)
    parser.close()
    parser.finish()
    if not parser.saw_wrapper:
        raise _structure_error("missing table wrapper")
    if parser.wrapper_count != 1:
        raise _structure_error("unexpected table wrapper count")
    if parser.malformed:
        raise _structure_error("malformed HTML")
    if len(parser.tables) != 1:
        raise _structure_error("unexpected table count")

    rows = parser.tables[0]
    layout = _validated_header(rows)
    items: list[GradeItem] = []
    category_row_count = 0
    for row in rows[1:]:
        if not row:
            raise _structure_error("empty row")
        if any(not cell.spans_valid or cell.rowspan != 1 or cell.colspan > 2 for cell in row):
            raise _structure_error("body span")
        if sum(cell.colspan for cell in row) != layout.effective_width:
            raise _structure_error("row width")
        if _is_category_row(row, layout):
            category_row_count += 1
            continue
        if not _is_item_row(row, layout):
            raise _structure_error("row geometry")

        values = _value_by_semantic(row, layout)
        label = values["grade_item"].text
        if label.lower() in _SUMMARY_LABELS:
            raise _structure_error("summary row")
        points = values["points"].text or None
        grade = values["grade"].text or None
        weight_cell = values.get("weight")
        weight = (weight_cell.text or None) if weight_cell is not None else None
        items.append(
            GradeItem(
                course_id=course_id,
                label=label,
                displayed_points=points,
                displayed_grade=grade,
                displayed_weight=weight,
                feedback_availability=_feedback_availability(values["feedback"]),
            )
        )

    return GradeListPage(
        items=tuple(items),
        category_row_count=category_row_count,
        header_effective_width=layout.effective_width,
        has_weight_column=layout.has_weight,
    )


def _validated_redirect_target(location: str, *, course_id: str) -> tuple[str, dict[str, str]]:
    try:
        parts = urlsplit(location)
    except ValueError:
        raise _structure_error("invalid redirect") from None
    if (
        parts.scheme
        or parts.netloc
        or parts.fragment
        or parts.path != _GRADES_MAIN_PATH
        or parts.query != f"ou={course_id}"
    ):
        raise _structure_error("unexpected redirect")
    return _GRADES_MAIN_PATH, {"ou": course_id}


class BrightspaceGradeProvider:
    """Read visible grade items through the established two-GET pathway."""

    def __init__(self, client: D2LHttpClient) -> None:
        self._client = client

    def list_grade_items(self, course_id: str) -> tuple[GradeItem, ...]:
        return self.get_grade_list(course_id).items

    def get_grade_list(self, course_id: str) -> GradeListPage:
        if not _is_course_id(course_id):
            raise ValueError("course_id must be a non-empty numeric Brightspace org-unit id")
        response = self._client.get_text_following_one_redirect(
            _GRADES_INDEX_PATH,
            params={"ou": course_id},
            validate_redirect=lambda location: _validated_redirect_target(
                location, course_id=course_id
            ),
            expected_content_type="text/html",
        )
        return parse_grade_list_html(response.text, course_id=course_id)
