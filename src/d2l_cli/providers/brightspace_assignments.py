"""Production parser and provider for Brightspace's Assignment ("Dropbox")
list.

Endpoint (Phase 2A reconnaissance; see docs/PROVIDER_NOTES.md, "Phase 2A --
Assignment Reconnaissance"): ``GET
/d2l/lms/dropbox/user/folders_list.d2l?ou=<course_id>``, a server-rendered
HTML page, not a JSON API. ``BrightspaceAssignmentProvider`` below calls
this through ``D2LHttpClient.get_text()`` (the Phase 2B HTML/text
transport path -- see ``d2l_cli.client.http``), never ``get()``.

This module deliberately does **not** reuse
``scripts/recon_lib.py``'s ``_StructuralHTMLParser``: that parser's entire
purpose is to discard text/attribute values and keep only shape/counts,
which is the opposite of what production parsing needs (extracting the
real assignment name/status/score for user-facing output). This is a
separate, narrow, from-scratch parser with its own contract. Extracted
text is real Brightspace content -- untrusted **data**, never treated as
instructions to this code (see docs/THREAT_MODEL.md, asset #13); nothing
here executes or interprets it, it is only assigned to plain ``str``
fields (or, for the due date, parsed by a fixed pattern into a
``datetime``).

Known limitation -- column positions are a POSITIONAL assumption, not
re-derived from live header text per response: Phase 2A's one live,
audited capture confirmed columns 0/1/2 as
``assignment_name``/``submission_status``/``score`` (column 3 remains
``unknown``) via ``scripts/recon_lib.py::classify_header_semantic``, but
that alias vocabulary lives in a dev-only script, not a shared production
module, so this parser does not (yet) re-run that classification against
each live response's actual header text. If a future capture shows a
different column order, this positional assumption -- and the case for
unifying the header-alias vocabulary into a shared production module --
should be revisited then, evidence-first.

Identity and the ``ou``/course-context cross-check: a row is only treated
as an Assignment if at least one ``<a href>`` anywhere in that row carries
a ``db`` query parameter shaped like every ``db``/``ou`` value Phase 2A
ever observed (digits only -- see ``_is_int_like_id``). This is
deliberate, not incidental: it is what keeps a divider/group row (observed
structurally as a one-cell row with no assignment link) from ever being
fabricated into a fake Assignment, and keeps a malformed/non-numeric
``db`` from becoming an identity this project has no evidence such values
ever take. ``course_id`` normally comes from the caller-supplied
parameter, not the row's own ``ou`` (present on some but not all observed
row links) -- but if that same identity-establishing link *also* carries a
well-formed ``ou`` that disagrees with the requested ``course_id``, the
row is skipped rather than silently labeled with either value: this
project has no live evidence such a mismatch ever occurs, so the
conservative choice is to drop the row rather than guess which course it
really belongs to. A missing ``ou`` falls back to the caller-supplied
``course_id``; a malformed or duplicate ``ou`` makes the identity ambiguous
and is rejected. Absolute, protocol-relative, fragmented, or otherwise
non-relative identity links are also rejected. Phase 3A also observed
dated four-cell rows with NO identity link at all; their meaning is
unknown, so they are ignored exactly like any other row without an
identity -- no id is ever invented for them.

Title and due date (Phase 3B; structure from docs/PROVIDER_NOTES.md, "Phase 3A" and
"Phase 3B"). The name cell can hold more than the title -- a due-date line
and availability text sit in later nodes of the same cell -- so the title is
NEVER built by joining every text node in the cell (that is the bug this
replaced: date/availability text leaked into ``Assignment.name``). It comes
from one structurally identified element:

1. the ``d2l-foldername-medium-font`` title container, if the cell has one
   (observed on every live row; the title inside it is either the identity
   link's ``strong`` or, on dated rows, a plain ``label > strong``); else
2. the cell's first identity (``db``) link; else
3. the cell's first text node.

The due date is read only from a text node in the name cell that (a) is NOT
part of the title, (b) sits directly in a ``strong`` that sits directly in a
``label`` -- the structure Phase 3A proved -- and (c) begins with the
marker ``Due on``. Its text must then match the one observed format exactly
(``Due on <Mon> <D>, <YYYY> <h>:<mm> <AM|PM>``) and be a real calendar
date/time; anything else -- no marker, no parseable date, two markers, a
date without the marker, availability text, a date in another column or
inside the title -- yields ``due_date = None`` rather than a guess. The
result is a timezone-NAIVE ``datetime`` (the page shows no timezone; none is
asserted). Availability text is never read, exposed, or used as a due date.
Title selection is structural, never date-regex cleanup, so a legitimate
title that mentions a month or "Due" is preserved verbatim.

Completeness guard (pagination follow-up): the selected positive page-size
ceiling is accepted only from the observed same-endpoint POST form and only
when its selector is unambiguous. Its comparison count is collected before
normalization, so linkless, duplicate, mismatched, and unfamiliar multi-cell
data rows cannot make the count artificially small. Reaching the ceiling means
"potentially truncated"; staying below it means only that this boundary was not
reached. No form is submitted and no next-page endpoint is inferred.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.models.assignment import Assignment
from d2l_cli.models.assignment_completeness import (
    AssignmentCompleteness,
    AssignmentCompletenessStatus,
    AssignmentListPage,
)

_NAME_COLUMN = 0
_SUBMISSION_STATUS_COLUMN = 1
_SCORE_COLUMN = 2

_FOLDERS_LIST_PATH = "/d2l/lms/dropbox/user/folders_list.d2l"

# Observed live (value-free structural inspection, Phase 3B): the title of
# every assignment row sits inside an element carrying this class token.
_TITLE_CONTAINER_CLASS = "d2l-foldername-medium-font"

# HTML void elements never have an end tag; they must not be tracked as
# open elements (the same rule scripts/recon_lib.py applies).
_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)

_DUE_MARKER_RE = re.compile(r"^due on\b", re.IGNORECASE | re.ASCII)
_DUE_TEXT_RE = re.compile(
    r"^due on (?P<mon>[a-z]{3}) (?P<day>[0-9]{1,2}), (?P<year>[0-9]{4}) "
    r"(?P<hour>[0-9]{1,2}):(?P<minute>[0-9]{2}) (?P<ampm>am|pm)$",
    re.IGNORECASE | re.ASCII,
)
_PAGE_SIZE_TEXT_RE = re.compile(
    r"\b(?:items?|assignments?|results?|rows?)\s+per\s+page\b",
    re.IGNORECASE | re.ASCII,
)
# Explicit table, not strptime: strptime's month/AM-PM names follow the
# process locale, which would make parsing environment-dependent.
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _is_int_like_id(value: str) -> bool:
    """Every ``db``/``ou`` value Phase 2A observed had an int-like shape
    (digits only) -- see docs/PROVIDER_NOTES.md. Used to reject an identity value
    this project has no evidence such IDs ever take, not to claim the
    provider guarantees this shape forever."""
    return bool(value) and value.isascii() and value.isdecimal()


@dataclass
class _RowIdentity:
    db: str
    ou: str | None


def _extract_row_identity(href: str) -> _RowIdentity | None:
    """Extract ``db`` (required, int-like) and ``ou`` (optional, only used
    if also int-like) from a single row link's query string. Returns
    ``None`` if this href doesn't carry a usable ``db``."""
    try:
        parts = urlsplit(href)
        params = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=100)
    except ValueError:
        return None
    if (
        parts.scheme
        or parts.netloc
        or parts.fragment
        or not parts.path.startswith("/")
        or "\\" in parts.path
    ):
        return None

    db_values = [value for key, value in params if key == "db"]
    ou_values = [value for key, value in params if key == "ou"]
    if len(db_values) != 1 or not _is_int_like_id(db_values[0]):
        return None
    if len(ou_values) > 1:
        return None
    ou = ou_values[0] if ou_values else None
    if ou is not None and not _is_int_like_id(ou):
        return None
    return _RowIdentity(db=db_values[0], ou=ou)


@dataclass
class _TextNode:
    """One non-blank text node inside a cell, with the structural context
    the title/due-date rules need."""

    text: str
    in_title_container: bool
    in_first_identity_link: bool
    parent_tag: str | None
    grandparent_tag: str | None


@dataclass
class _OpenElement:
    tag: str
    title_container: bool
    first_identity_link: bool


@dataclass
class _CellState:
    tag: str
    text_chunks: list[str] = field(default_factory=list)
    nodes: list[_TextNode] = field(default_factory=list)
    open_elements: list[_OpenElement] = field(default_factory=list)
    has_title_container: bool = False
    has_identity_link: bool = False


@dataclass
class _TableState:
    current_row: list[_CellState] | None = None
    current_cell: _CellState | None = None
    row_identity: _RowIdentity | None = None


@dataclass
class _FormState:
    method: str
    same_endpoint: bool
    text_chunks: list[str] = field(default_factory=list)
    select_results: list[tuple[bool, int | None]] = field(default_factory=list)


@dataclass
class _PageSizeSelectState:
    attribute_signal: bool
    text_chunks: list[str] = field(default_factory=list)
    selected_values: list[int | None] = field(default_factory=list)
    malformed: bool = False
    option_open: bool = False
    option_selected: bool = False
    option_value: int | None = None
    option_text: list[str] = field(default_factory=list)


def _extract_title(cell: _CellState) -> tuple[str, frozenset[int]]:
    """The assignment title and the indexes of the text nodes it came from
    (so the due-date rule can exclude them). See the module docstring for
    the three-step structural rule; nothing here inspects text content."""
    if cell.has_title_container:
        indexes = [i for i, n in enumerate(cell.nodes) if n.in_title_container]
    elif cell.has_identity_link:
        indexes = [i for i, n in enumerate(cell.nodes) if n.in_first_identity_link]
    else:
        indexes = [0] if cell.nodes else []
    text = _normalize_text("".join(cell.nodes[i].text for i in indexes))
    return text, frozenset(indexes)


def _parse_due_text(text: str) -> datetime | None:
    """Parse the single observed due-date format into a timezone-naive
    ``datetime``; ``None`` for anything else (never a guess)."""
    match = _DUE_TEXT_RE.match(text)
    if match is None:
        return None
    month = _MONTHS.get(match.group("mon").lower())
    hour12 = int(match.group("hour"))
    minute = int(match.group("minute"))
    if month is None or not 1 <= hour12 <= 12 or minute > 59:
        return None
    hour = hour12 % 12 + (12 if match.group("ampm").lower() == "pm" else 0)
    try:
        return datetime(int(match.group("year")), month, int(match.group("day")), hour, minute)
    except ValueError:  # impossible calendar date, e.g. Feb 30
        return None


def _extract_due_date(cell: _CellState, title_indexes: frozenset[int]) -> datetime | None:
    """The due date from the Phase 3A-proven structure: a ``label > strong``
    text node, outside the title, starting with the "Due on" marker. Zero or
    several marker nodes (ambiguous) -> ``None``."""
    candidates = [
        _normalize_text(node.text)
        for index, node in enumerate(cell.nodes)
        if index not in title_indexes
        and not node.in_title_container
        and not node.in_first_identity_link
        and node.parent_tag == "strong"
        and node.grandparent_tag == "label"
        and _DUE_MARKER_RE.match(_normalize_text(node.text))
    ]
    if len(candidates) != 1:
        return None
    return _parse_due_text(candidates[0])


class _AssignmentListHTMLParser(HTMLParser):
    """Extracts real Assignment rows from a ``d2l-table-wrapper``-scoped
    table. See module docstring for the identity/column-position/title/
    due-date contract.
    """

    def __init__(self, *, course_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self._course_id = course_id
        self.assignments: list[Assignment] = []
        self.saw_wrapper = False
        self._seen_ids: set[str] = set()

        # Completeness evidence is collected before normalization. A row with
        # an identity link counts even if it is later rejected as a duplicate
        # or course mismatch. Linkless multi-cell rows count conservatively;
        # Phase 3A observed a four-cell assignment-like instance of that shape.
        self.observed_row_count = 0
        self.selected_page_sizes: list[int | None] = []

        self._wrapper_depth = 0
        self._table_stack: list[_TableState] = []
        self._form_stack: list[_FormState] = []
        self._page_size_select: _PageSizeSelectState | None = None

    def _active_table(self) -> _TableState | None:
        return self._table_stack[-1] if self._table_stack else None

    def _finalize_cell(self) -> None:
        table = self._active_table()
        if table is None or table.current_cell is None or table.current_row is None:
            return
        table.current_row.append(table.current_cell)
        table.current_cell = None

    def _finalize_row(self) -> None:
        table = self._active_table()
        if table is None or table.current_row is None:
            return
        self._finalize_cell()
        cells = table.current_row
        identity = table.row_identity
        data_cell_count = sum(cell.tag == "td" for cell in cells)
        structured_title = any(cell.tag == "td" and cell.has_title_container for cell in cells)
        if identity is not None or data_cell_count >= 2 or structured_title:
            # Counting an unfamiliar multi-cell data row is conservative: it
            # can produce a warning sooner, but cannot hide a reached boundary.
            # One-cell divider/empty rows remain excluded unless they carry
            # the live-observed assignment title-container structure.
            self.observed_row_count += 1
        if identity is not None and identity.db not in self._seen_ids:
            # A well-formed ou that disagrees with the requested course
            # context is dropped rather than guessed at -- see module
            # docstring. A missing ou is not a mismatch; malformed or
            # ambiguous values were rejected before an identity was made.
            mismatched = identity.ou is not None and identity.ou != self._course_id
            if not mismatched:
                self._seen_ids.add(identity.db)

                def cell_text(index: int) -> str | None:
                    if index >= len(cells):
                        return None
                    text = _normalize_text("".join(cells[index].text_chunks))
                    return text or None

                name = ""
                due_date: datetime | None = None
                if _NAME_COLUMN < len(cells):
                    name, title_indexes = _extract_title(cells[_NAME_COLUMN])
                    due_date = _extract_due_date(cells[_NAME_COLUMN], title_indexes)

                self.assignments.append(
                    Assignment(
                        id=identity.db,
                        course_id=self._course_id,
                        name=name,
                        submission_status=cell_text(_SUBMISSION_STATUS_COLUMN),
                        score=cell_text(_SCORE_COLUMN),
                        due_date=due_date,
                    )
                )
        table.current_row = None
        table.row_identity = None

    def _finalize_table(self) -> None:
        if not self._table_stack:
            return
        self._finalize_row()
        self._table_stack.pop()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: (v or "") for k, v in attrs}

        self._track_page_size_start(tag, attr_map)

        if tag == "d2l-table-wrapper":
            self._wrapper_depth += 1
            self.saw_wrapper = True
        elif tag == "table" and self._wrapper_depth > 0:
            self._table_stack.append(_TableState())
        elif tag == "tr":
            table = self._active_table()
            if table is not None:
                if table.current_row is not None:
                    self._finalize_row()  # missing </tr> (tag-soup) -- flush first
                table.current_row = []
                table.row_identity = None
        elif tag in ("th", "td"):
            table = self._active_table()
            if table is not None and table.current_row is not None:
                if table.current_cell is not None:
                    self._finalize_cell()  # missing </th>/</td> -- flush first
                table.current_cell = _CellState(tag=tag)
        else:
            self._track_open_element(tag, attr_map)

        if tag == "a" and "href" in attr_map:
            table = self._active_table()
            if table is not None and table.row_identity is None:
                identity = _extract_row_identity(attr_map["href"])
                if identity is not None:
                    table.row_identity = identity

    def _track_open_element(self, tag: str, attr_map: dict[str, str]) -> None:
        """Record structural context (title container / first identity link)
        for text nodes inside the current cell."""
        table = self._active_table()
        cell = table.current_cell if table is not None else None
        if cell is None or tag in _VOID_ELEMENTS:
            return
        is_container = _TITLE_CONTAINER_CLASS in attr_map.get("class", "").split()
        is_first_identity_link = False
        if tag == "a" and not cell.has_identity_link and "href" in attr_map:
            if _extract_row_identity(attr_map["href"]) is not None:
                cell.has_identity_link = True
                is_first_identity_link = True
        if is_container:
            cell.has_title_container = True
        cell.open_elements.append(_OpenElement(tag, is_container, is_first_identity_link))

    @staticmethod
    def _same_list_action(action: str | None) -> bool:
        if action is None or not action.strip():
            return True
        path = urlsplit(action).path
        return path in (
            _FOLDERS_LIST_PATH,
            "folders_list.d2l",
            "./folders_list.d2l",
        )

    @staticmethod
    def _is_page_size_select(attrs: dict[str, str]) -> bool:
        signal = " ".join(
            attrs.get(name, "") for name in ("class", "id", "name", "aria-label", "title")
        ).lower()
        compact = re.sub(r"[^a-z]", "", signal)
        return (
            "itemsperpage" in compact
            or "rowsperpage" in compact
            or "pagesize" in compact
            or "perpage" in compact
        )

    @staticmethod
    def _positive_int(value: str) -> int | None:
        normalized = _normalize_text(value)
        if (
            not normalized
            or len(normalized) > 9
            or not normalized.isascii()
            or not normalized.isdecimal()
        ):
            return None
        parsed = int(normalized)
        return parsed if parsed > 0 else None

    def _track_page_size_start(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == "form":
            self._form_stack.append(
                _FormState(
                    method=attrs.get("method", "GET").upper(),
                    same_endpoint=self._same_list_action(attrs.get("action")),
                )
            )
            return

        form = self._form_stack[-1] if self._form_stack else None
        if tag == "select":
            if self._page_size_select is not None:
                # Nested selects are invalid HTML; keep this candidate but
                # ensure it can never become trusted boundary evidence.
                self._page_size_select.malformed = True
                return
            in_observed_form = form is not None and form.method == "POST" and form.same_endpoint
            if in_observed_form:
                self._page_size_select = _PageSizeSelectState(
                    attribute_signal=self._is_page_size_select(attrs)
                )
            return

        select = self._page_size_select
        if tag == "option" and select is not None:
            if select.option_open:
                select.malformed = True
                self._finish_selected_option()
            select.option_open = True
            select.option_selected = "selected" in attrs
            select.option_value = self._positive_int(attrs.get("value", ""))
            select.option_text = []

    def _finish_selected_option(self) -> None:
        select = self._page_size_select
        if select is None:
            return
        if not select.option_selected:
            select.option_open = False
            select.option_value = None
            select.option_text = []
            return
        value = select.option_value
        if value is None:
            value = self._positive_int("".join(select.option_text))
        select.selected_values.append(value)
        select.option_open = False
        select.option_selected = False
        select.option_value = None
        select.option_text = []

    def _finish_page_size_select(self) -> None:
        select = self._page_size_select
        if select is None:
            return
        self._finish_selected_option()
        value = (
            select.selected_values[0]
            if not select.malformed and len(select.selected_values) == 1
            else None
        )
        form = self._form_stack[-1] if self._form_stack else None
        if form is not None:
            select_text_signal = _PAGE_SIZE_TEXT_RE.search(
                _normalize_text(" ".join(select.text_chunks))
            )
            form.select_results.append(
                (select.attribute_signal or select_text_signal is not None, value)
            )
        self._page_size_select = None

    def _finish_page_size_form(self, form: _FormState) -> None:
        if form.method != "POST" or not form.same_endpoint:
            return
        explicit = [value for signal, value in form.select_results if signal]
        if explicit:
            self.selected_page_sizes.extend(explicit)
            return
        form_has_signal = _PAGE_SIZE_TEXT_RE.search(_normalize_text(" ".join(form.text_chunks)))
        if form_has_signal is None:
            return
        if len(form.select_results) == 1:
            self.selected_page_sizes.append(form.select_results[0][1])
        else:
            # A label with zero or several possible controls is ambiguous.
            self.selected_page_sizes.append(None)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option":
            if self._page_size_select is not None and self._page_size_select.option_open:
                self._finish_selected_option()
        elif tag == "select":
            self._finish_page_size_select()
        elif tag == "form":
            if self._page_size_select is not None:
                # A candidate select crossing a form boundary is malformed.
                self._page_size_select.malformed = True
                self._finish_page_size_select()
            if self._form_stack:
                form = self._form_stack.pop()
                self._finish_page_size_form(form)

        if tag in ("th", "td"):
            self._finalize_cell()
        elif tag == "tr":
            self._finalize_row()
        elif tag == "table":
            self._finalize_table()
        elif tag == "d2l-table-wrapper":
            self._wrapper_depth = max(0, self._wrapper_depth - 1)
        else:
            table = self._active_table()
            cell = table.current_cell if table is not None else None
            if cell is not None and any(e.tag == tag for e in cell.open_elements):
                while cell.open_elements:
                    if cell.open_elements.pop().tag == tag:
                        break

    def handle_data(self, data: str) -> None:
        # Assignment titles are untrusted data and must never make an
        # unrelated select look like pagination. Only form text outside the
        # assignment table wrapper can supply the observed page-size label.
        if self._form_stack and self._wrapper_depth == 0:
            self._form_stack[-1].text_chunks.append(data)
        if self._page_size_select is not None:
            self._page_size_select.text_chunks.append(data)
        if self._page_size_select is not None and self._page_size_select.option_selected:
            self._page_size_select.option_text.append(data)
        table = self._active_table()
        if table is None or table.current_cell is None:
            return
        cell = table.current_cell
        cell.text_chunks.append(data)
        if data.strip():
            stack = cell.open_elements
            cell.nodes.append(
                _TextNode(
                    text=data,
                    in_title_container=any(e.title_container for e in stack),
                    in_first_identity_link=any(e.first_identity_link for e in stack),
                    parent_tag=stack[-1].tag if stack else None,
                    grandparent_tag=stack[-2].tag if len(stack) >= 2 else None,
                )
            )

    def finalize(self) -> None:
        # Drain anything left open by a truncated/malformed document rather
        # than silently losing a trailing assignment row.
        while self._table_stack:
            self._finalize_table()
        if self._page_size_select is not None:
            # Truncated selector markup is not reliable evidence.
            self.selected_page_sizes.append(None)
            self._page_size_select = None

    def completeness(self) -> AssignmentCompleteness:
        ceiling: int | None = None
        if len(self.selected_page_sizes) == 1 and self.selected_page_sizes[0] is not None:
            ceiling = self.selected_page_sizes[0]
        if ceiling is None:
            status = AssignmentCompletenessStatus.UNKNOWN
        elif self.observed_row_count >= ceiling:
            status = AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED
        else:
            status = AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY
        return AssignmentCompleteness(
            status=status,
            observed_row_count=self.observed_row_count,
            page_size_ceiling=ceiling,
        )


def parse_assignment_list_html(html: str, *, course_id: str) -> tuple[Assignment, ...]:
    """Parse a Brightspace Assignment-list HTML page into normalized
    ``Assignment`` records, scoped to ``course_id``.

    Raises ``ProviderEndpointChangedError`` if the page contains no
    ``d2l-table-wrapper`` element at all -- Phase 2A's live evidence
    established this wrapper structure is always present on this page, so
    its total absence indicates the page structure changed, not that the
    course has no assignments (which is instead represented by returning
    an empty tuple: a wrapper was present, just with no ``db``-identified
    rows inside it).
    """
    return parse_assignment_list_page(html, course_id=course_id).assignments


def parse_assignment_list_page(html: str, *, course_id: str) -> AssignmentListPage:
    """Parse assignments and assess the observed response's page boundary."""
    parser = _AssignmentListHTMLParser(course_id=course_id)
    parser.feed(html)
    parser.close()
    parser.finalize()
    if not parser.saw_wrapper:
        raise ProviderEndpointChangedError(
            "assignment list page did not contain the expected "
            "d2l-table-wrapper structure (see docs/PROVIDER_NOTES.md, 'Phase 2A -- "
            "Assignment Reconnaissance')"
        )
    return AssignmentListPage(
        assignments=tuple(parser.assignments),
        completeness=parser.completeness(),
    )


class BrightspaceAssignmentProvider:
    """GET-only adapter around the HTML-rendered Assignment ("Dropbox")
    list. The Course-vertical analogue of ``BrightspaceCourseProvider``,
    but over ``D2LHttpClient.get_text()`` instead of ``get()``, since this
    endpoint is server-rendered HTML, not JSON (see docs/PROVIDER_NOTES.md, "Phase
    2A -- Assignment Reconnaissance").

    Never calls the submit/feedback/detail routes observed in row links --
    this provider only ever requests the one proven list endpoint.
    """

    def __init__(self, client: D2LHttpClient) -> None:
        self._client = client

    def list_assignments(self, course_id: str) -> tuple[Assignment, ...]:
        """List assignments for the given course (an explicit Brightspace
        org-unit id -- no fuzzy/title-based course lookup is performed
        here or anywhere above this layer).

        Raises ``ValueError`` if ``course_id`` isn't shaped like every
        ``ou`` value Phase 2A ever observed (digits only) -- rejected
        before a request is even built, not after a confusing provider
        error. Raises ``ProviderEndpointChangedError`` (via the parser) if
        the response isn't the expected Assignment-list structure -- see
        ``parse_assignment_list_html``. Any other ``D2LHttpClient`` failure
        (auth, network, TLS, unexpected status, wrong content-type)
        propagates unchanged.
        """
        return self.get_assignment_list(course_id).assignments

    def get_assignment_list(self, course_id: str) -> AssignmentListPage:
        """Return assignments and evidence-bounded completeness metadata."""
        if not course_id or not _is_int_like_id(course_id):
            raise ValueError("course_id must be a non-empty numeric Brightspace org-unit id")
        response = self._client.get_text(
            _FOLDERS_LIST_PATH,
            params={"ou": course_id},
            expected_content_type="text/html",
        )
        return parse_assignment_list_page(response.text, course_id=course_id)
