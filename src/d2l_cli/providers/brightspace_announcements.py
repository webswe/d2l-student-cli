"""GET-only provider and fail-closed parser for Brightspace News listings."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit

from d2l_cli.announcement_sanitizer import (
    AnnouncementSanitizationError,
    sanitize_announcement_body,
    sanitize_plain_text,
)
from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.models.announcement import Announcement, AnnouncementListPage

_NEWS_MAIN_PATH = "/d2l/lms/news/main.d2l"
_DETAIL_PATH_RE = re.compile(r"/d2l/le/news/([0-9]+)/([0-9]+)/view")
_MAX_PAGE_CHARS = 5_000_000
_MAX_ELEMENTS = 100_000
_MAX_ROWS = 5_000
_MAX_CELLS = 20_000
_MAX_TITLE_CHARS = 20_000
_MAX_SPAN = 2
_UNSAFE_TITLE_TAGS = frozenset(
    {"applet", "embed", "form", "iframe", "input", "math", "object", "script", "style", "svg"}
)
_UNSUPPORTED_ROW_MARKERS = (
    "collapse-item",
    "collapsed",
    "expand-item",
    "expanded",
    "pin-item",
    "pinned",
    "summary",
)


def _structure_error(code: str) -> ProviderEndpointChangedError:
    return ProviderEndpointChangedError(
        f"Announcements page structure is unsupported (ANNOUNCEMENTS_{code.upper()})"
    )


def _is_course_id(value: str) -> bool:
    return bool(value) and value.isascii() and value.isdecimal()


def _attribute_values(attrs: list[tuple[str, str | None]], name: str) -> list[str | None]:
    return [value for key, value in attrs if key.lower() == name]


def _parse_span(attrs: list[tuple[str, str | None]], name: str) -> tuple[int, bool]:
    values = _attribute_values(attrs, name)
    if not values:
        return 1, True
    if len(values) != 1:
        return 1, False
    value = values[0]
    if value is None or not value.isascii() or not value.isdecimal() or len(value) > 1:
        return 1, False
    span = int(value)
    return (span, True) if 1 <= span <= _MAX_SPAN else (1, False)


def _has_hidden_attribute(attrs: list[tuple[str, str | None]]) -> bool:
    if any(name.lower() == "hidden" for name, _value in attrs):
        return True
    if any(
        name.lower() == "aria-hidden" and (value or "").strip().lower() == "true"
        for name, value in attrs
    ):
        return True
    return any(
        "display:none" in re.sub(r"\s+", "", (value or "").lower())
        or "visibility:hidden" in re.sub(r"\s+", "", (value or "").lower())
        for value in _attribute_values(attrs, "style")
    )


def _row_has_unsupported_marker(attrs: list[tuple[str, str | None]]) -> bool:
    class_values = _attribute_values(attrs, "class")
    tokens = {token.lower() for value in class_values for token in (value or "").split()}
    return any(marker in token for marker in _UNSUPPORTED_ROW_MARKERS for token in tokens)


@dataclass(slots=True)
class _DetailLink:
    announcement_id: str
    detail_reference: str
    text_chunks: list[str] = field(default_factory=list)
    text_char_count: int = 0
    unsafe_title_markup: bool = False


@dataclass(slots=True)
class _Cell:
    tag: str
    colspan: int
    rowspan: int
    spans_valid: bool
    detail_links: list[_DetailLink] = field(default_factory=list)
    body_html_values: list[str | None] = field(default_factory=list)
    nested_body_block: bool = False
    current_link: _DetailLink | None = None


@dataclass(slots=True)
class _Row:
    cells: list[_Cell] = field(default_factory=list)
    unsupported_marker: bool = False
    nested_announcement_structure: bool = False

    @property
    def detail_links(self) -> list[_DetailLink]:
        return [link for cell in self.cells for link in cell.detail_links]

    @property
    def body_block_count(self) -> int:
        return sum(len(cell.body_html_values) for cell in self.cells)


@dataclass(slots=True)
class _Table:
    in_post_form: bool
    rows: list[_Row] = field(default_factory=list)
    current_row: _Row | None = None
    current_cell: _Cell | None = None


def _looks_like_detail_link(href: str) -> bool:
    try:
        return urlsplit(href).path.startswith("/d2l/le/news/")
    except ValueError:
        return "/d2l/le/news/" in href


def _detail_href(attrs: list[tuple[str, str | None]]) -> str | None:
    hrefs = _attribute_values(attrs, "href")
    candidates = [href for href in hrefs if href and _looks_like_detail_link(href)]
    if not candidates:
        return None
    if len(hrefs) != 1 or len(candidates) != 1:
        raise _structure_error("DETAIL_LINK")
    return candidates[0]


def _validated_detail_link(href: str, *, course_id: str) -> _DetailLink:
    try:
        parts = urlsplit(href)
    except ValueError:
        raise _structure_error("DETAIL_LINK") from None
    if parts.scheme or parts.netloc or parts.fragment:
        raise _structure_error("DETAIL_LINK")
    match = _DETAIL_PATH_RE.fullmatch(parts.path)
    if match is None:
        raise _structure_error("DETAIL_LINK")
    if match.group(1) != course_id:
        raise _structure_error("COURSE_ASSOCIATION")
    params = parse_qsl(parts.query, keep_blank_values=True)
    if len(params) != 1 or params[0][0] != "ou":
        raise _structure_error("DETAIL_LINK_QUERY")
    if params[0][1] != course_id:
        raise _structure_error("COURSE_ASSOCIATION")
    return _DetailLink(
        announcement_id=match.group(2),
        detail_reference=parts.path,
    )


def _validate_form_action(action: str, *, course_id: str) -> None:
    try:
        parts = urlsplit(action)
    except ValueError:
        raise _structure_error("PAGE_FORM_ACTION") from None
    if parts.scheme or parts.netloc or parts.fragment or parts.path != "main.d2l":
        raise _structure_error("PAGE_FORM_ACTION")
    params = parse_qsl(parts.query, keep_blank_values=True)
    if len(params) != 1 or params[0][0] != "ou":
        raise _structure_error("PAGE_FORM_ACTION")
    if params[0][1] != course_id:
        raise _structure_error("COURSE_ASSOCIATION")


class _AnnouncementListingHTMLParser(HTMLParser):
    def __init__(self, *, course_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self._course_id = course_id
        self.tables: list[_Table] = []
        self.form_count = 0
        self.post_form_count = 0
        self.course_validated_form_count = 0
        self.orphan_detail_link_count = 0
        self.malformed = False
        self.element_count = 0
        self.row_count = 0
        self.cell_count = 0
        self._form_depth = 0
        self._current_form_is_post = False
        self._table_depth = 0
        self._table: _Table | None = None
        self._body_block_depth = 0

    def _finish_link(self) -> None:
        if self._table is None or self._table.current_cell is None:
            return
        cell = self._table.current_cell
        if cell.current_link is not None:
            cell.detail_links.append(cell.current_link)
            cell.current_link = None

    def _finish_cell(self) -> None:
        if self._table is None or self._table.current_cell is None:
            return
        if self._table.current_cell.current_link is not None or self._body_block_depth:
            self.malformed = True
            self._finish_link()
            self._body_block_depth = 0
        if self._table.current_row is None:
            self.malformed = True
        else:
            self._table.current_row.cells.append(self._table.current_cell)
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
        self.tables.append(self._table)
        self._table = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        self.element_count += 1
        if self.element_count > _MAX_ELEMENTS:
            raise _structure_error("ELEMENT_LIMIT")

        if normalized_tag == "form":
            if self._form_depth:
                self.malformed = True
            self._form_depth += 1
            self.form_count += 1
            methods = _attribute_values(attrs, "method")
            self._current_form_is_post = (
                len(methods) == 1 and (methods[0] or "").strip().lower() == "post"
            )
            if self._current_form_is_post:
                self.post_form_count += 1
                actions = _attribute_values(attrs, "action")
                if len(actions) != 1 or actions[0] is None:
                    raise _structure_error("PAGE_FORM_ACTION")
                _validate_form_action(actions[0], course_id=self._course_id)
                self.course_validated_form_count += 1
            return

        if normalized_tag == "table":
            if self._table_depth == 0:
                self._table = _Table(
                    in_post_form=self._form_depth == 1 and self._current_form_is_post
                )
            self._table_depth += 1
            return

        if self._table_depth != 1 or self._table is None:
            if normalized_tag == "a":
                href = _detail_href(attrs)
                if href is not None:
                    _validated_detail_link(href, course_id=self._course_id)
                    self.orphan_detail_link_count += 1
            elif (
                normalized_tag == "d2l-html-block"
                and self._table is not None
                and self._table.current_row is not None
            ):
                self._table.current_row.nested_announcement_structure = True
            return

        if normalized_tag == "tr":
            if self._table.current_row is not None:
                self.malformed = True
                self._finish_row()
            self.row_count += 1
            if self.row_count > _MAX_ROWS:
                raise _structure_error("ROW_LIMIT")
            self._table.current_row = _Row(unsupported_marker=_row_has_unsupported_marker(attrs))
            return

        if normalized_tag in {"th", "td"}:
            if self._table.current_row is None:
                self.malformed = True
                return
            self._finish_cell()
            self.cell_count += 1
            if self.cell_count > _MAX_CELLS:
                raise _structure_error("CELL_LIMIT")
            colspan, colspan_valid = _parse_span(attrs, "colspan")
            rowspan, rowspan_valid = _parse_span(attrs, "rowspan")
            self._table.current_cell = _Cell(
                tag=normalized_tag,
                colspan=colspan,
                rowspan=rowspan,
                spans_valid=colspan_valid and rowspan_valid,
            )
            return

        row = self._table.current_row
        cell = self._table.current_cell
        if row is not None and _row_has_unsupported_marker(attrs):
            row.unsupported_marker = True
        if cell is None:
            return

        if normalized_tag == "a":
            if cell.current_link is not None:
                self.malformed = True
            href = _detail_href(attrs)
            if href is not None:
                cell.current_link = _validated_detail_link(href, course_id=self._course_id)
                if _has_hidden_attribute(attrs):
                    cell.current_link.unsafe_title_markup = True
            return

        if cell.current_link is not None and (
            normalized_tag in _UNSAFE_TITLE_TAGS or _has_hidden_attribute(attrs)
        ):
            cell.current_link.unsafe_title_markup = True

        if normalized_tag == "d2l-html-block":
            if self._body_block_depth:
                cell.nested_body_block = True
            self._body_block_depth += 1
            html_values = _attribute_values(attrs, "html")
            cell.body_html_values.append(html_values[0] if len(html_values) == 1 else None)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag == "a" and self._table_depth == 1:
            self._finish_link()
        elif normalized_tag == "d2l-html-block" and self._body_block_depth:
            self._body_block_depth -= 1
        elif normalized_tag in {"th", "td"} and self._table_depth == 1:
            self._finish_cell()
        elif normalized_tag == "tr" and self._table_depth == 1:
            self._finish_row()
        elif normalized_tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._finish_table()
        elif normalized_tag == "form" and self._form_depth:
            if self._table_depth:
                self.malformed = True
            self._form_depth -= 1
            if self._form_depth == 0:
                self._current_form_is_post = False

    def handle_data(self, data: str) -> None:
        if (
            self._table_depth == 1
            and self._table is not None
            and self._table.current_cell is not None
            and self._table.current_cell.current_link is not None
        ):
            link = self._table.current_cell.current_link
            link.text_char_count += len(data)
            if link.text_char_count > _MAX_TITLE_CHARS:
                raise _structure_error("TITLE_LIMIT")
            link.text_chunks.append(data)

    def finish(self) -> None:
        if (
            self._form_depth
            or self._table_depth
            or self._table is not None
            or self._body_block_depth
        ):
            self.malformed = True


def _row_width(row: _Row) -> int:
    return sum(cell.colspan for cell in row.cells)


def _validate_item_row(row: _Row) -> _DetailLink:
    links = row.detail_links
    if len(links) != 1:
        raise _structure_error("ITEM_LINK_COUNT")
    if len(row.cells) != 2 or _row_width(row) != 2:
        raise _structure_error("ITEM_ROW_GEOMETRY")
    if row.cells[0].tag != "th" or row.cells[1].tag != "td":
        raise _structure_error("ITEM_CELL_TAGS")
    if any(not cell.spans_valid or cell.colspan != 1 or cell.rowspan != 1 for cell in row.cells):
        raise _structure_error("ITEM_SPAN")
    if row.cells[0].detail_links != links or row.cells[1].detail_links:
        raise _structure_error("ITEM_LINK_PLACEMENT")
    if row.body_block_count:
        raise _structure_error("ITEM_BODY_BLOCK")
    if row.nested_announcement_structure:
        raise _structure_error("ITEM_NESTED_ANNOUNCEMENT_STRUCTURE")
    if row.unsupported_marker:
        raise _structure_error("SPECIAL_ITEM_ROW")
    link = links[0]
    if link.unsafe_title_markup:
        raise _structure_error("UNSAFE_TITLE_MARKUP")
    return link


def _validate_content_row(row: _Row) -> str:
    if len(row.cells) != 1 or _row_width(row) != 2:
        raise _structure_error("CONTENT_ROW_GEOMETRY")
    cell = row.cells[0]
    if cell.tag != "td":
        raise _structure_error("CONTENT_CELL_TAG")
    if not cell.spans_valid or cell.colspan != 2 or cell.rowspan != 1:
        raise _structure_error("CONTENT_SPAN")
    if row.detail_links:
        raise _structure_error("CONTENT_LINK")
    if row.body_block_count != 1:
        raise _structure_error("CONTENT_BLOCK_COUNT")
    if cell.nested_body_block:
        raise _structure_error("NESTED_CONTENT_BLOCK")
    if row.unsupported_marker:
        raise _structure_error("SPECIAL_CONTENT_ROW")
    if row.nested_announcement_structure:
        raise _structure_error("CONTENT_NESTED_ANNOUNCEMENT_STRUCTURE")
    body_html = cell.body_html_values[0]
    if body_html is None:
        raise _structure_error("CONTENT_ATTRIBUTE")
    return body_html


def parse_announcement_list_html(html: str, *, course_id: str) -> AnnouncementListPage:
    """Parse the verified two-row News layout without returning partial data."""

    if not _is_course_id(course_id):
        raise ValueError("course_id must be a non-empty numeric Brightspace org-unit id")
    if len(html) > _MAX_PAGE_CHARS:
        raise _structure_error("PAGE_LIMIT")
    parser = _AnnouncementListingHTMLParser(course_id=course_id)
    parser.feed(html)
    parser.close()
    parser.finish()
    if parser.malformed:
        raise _structure_error("MALFORMED_HTML")
    if (
        parser.form_count != 1
        or parser.post_form_count != 1
        or parser.course_validated_form_count != 1
    ):
        raise _structure_error("PAGE_FORM")
    post_form_tables = [table for table in parser.tables if table.in_post_form]
    if not post_form_tables:
        raise _structure_error("PAGE_TABLE")
    if parser.orphan_detail_link_count:
        raise _structure_error("ORPHAN_DETAIL_LINK")

    relevant_tables = [
        table for table in post_form_tables if any(row.detail_links for row in table.rows)
    ]
    if len(relevant_tables) > 1:
        raise _structure_error("ANNOUNCEMENT_TABLE_COUNT")
    if not relevant_tables:
        return AnnouncementListPage(())

    table = relevant_tables[0]
    items: list[Announcement] = []
    identities: set[str] = set()
    consumed_content_rows: set[int] = set()
    for index, row in enumerate(table.rows):
        if index in consumed_content_rows:
            continue
        if row.detail_links:
            link = _validate_item_row(row)
            content_index = index + 1
            if content_index >= len(table.rows):
                raise _structure_error("MISSING_CONTENT_ROW")
            body_html = _validate_content_row(table.rows[content_index])
            consumed_content_rows.add(content_index)
            if link.announcement_id in identities:
                raise _structure_error("DUPLICATE_IDENTITY")
            title = sanitize_plain_text("".join(link.text_chunks), preserve_lines=False)
            if not title:
                raise _structure_error("EMPTY_TITLE")
            try:
                body = sanitize_announcement_body(body_html)
            except AnnouncementSanitizationError as exc:
                raise _structure_error(f"BODY_{exc.code}") from None
            identities.add(link.announcement_id)
            items.append(
                Announcement(
                    course_id=course_id,
                    announcement_id=link.announcement_id,
                    title=title,
                    body=body,
                    detail_reference=link.detail_reference,
                )
            )
            continue
        if row.body_block_count:
            raise _structure_error("ORPHAN_CONTENT_ROW")
        if row.unsupported_marker:
            raise _structure_error("SPECIAL_ROW")

    return AnnouncementListPage(tuple(items))


class BrightspaceAnnouncementProvider:
    """Read visible announcements through the established single-GET route."""

    def __init__(self, client: D2LHttpClient) -> None:
        self._client = client

    def list_announcements(self, course_id: str) -> tuple[Announcement, ...]:
        return self.get_announcement_list(course_id).items

    def get_announcement_list(self, course_id: str) -> AnnouncementListPage:
        if not _is_course_id(course_id):
            raise ValueError("course_id must be a non-empty numeric Brightspace org-unit id")
        response = self._client.get_text(
            _NEWS_MAIN_PATH,
            params={"ou": course_id},
            expected_content_type="text/html",
        )
        return parse_announcement_list_html(response.text, course_id=course_id)
