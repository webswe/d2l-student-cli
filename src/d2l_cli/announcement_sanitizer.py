"""Bounded HTML-to-text sanitization for untrusted announcement bodies.

The sanitizer is deliberately not a renderer. It never loads resources,
follows links, interprets CSS, or exposes markup. Active/hidden subtrees are
discarded, readable block boundaries are retained, and terminal control
sequences are removed from the final plain text.
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser

MAX_ANNOUNCEMENT_HTML_CHARS = 200_000
MAX_ANNOUNCEMENT_TEXT_CHARS = 100_000
MAX_ANNOUNCEMENT_ELEMENTS = 10_000
MAX_ANNOUNCEMENT_NESTING = 128

_ACTIVE_OR_HIDDEN_TAGS = frozenset(
    {
        "applet",
        "audio",
        "button",
        "canvas",
        "embed",
        "form",
        "iframe",
        "input",
        "link",
        "math",
        "meta",
        "noscript",
        "object",
        "option",
        "script",
        "select",
        "source",
        "style",
        "svg",
        "template",
        "textarea",
        "track",
        "video",
    }
)
_VOID_TAGS = frozenset(
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
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
_ANSI_OSC_RE = re.compile(r"(?:\x1b\]|\x9d).*?(?:\x07|\x1b\\|\x9c)", re.DOTALL)
_ANSI_STRING_RE = re.compile(r"(?:\x1b[PX^_]|[\x90\x98\x9e\x9f]).*?(?:\x1b\\|\x9c)", re.DOTALL)
_ANSI_CSI_RE = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")
_ANSI_SINGLE_RE = re.compile(r"\x1b[@-_]")


class AnnouncementSanitizationError(ValueError):
    """A fixed-code refusal to process an unsafe or pathological body."""

    def __init__(self, code: str) -> None:
        super().__init__("announcement body could not be safely converted to text")
        self.code = code


def _attribute_values(attrs: list[tuple[str, str | None]], name: str) -> list[str | None]:
    return [value for key, value in attrs if key.lower() == name]


def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    names = {name.lower() for name, _value in attrs}
    if "hidden" in names:
        return True
    aria_hidden = _attribute_values(attrs, "aria-hidden")
    if any((value or "").strip().lower() == "true" for value in aria_hidden):
        return True
    styles = _attribute_values(attrs, "style")
    return any(
        "display:none" in re.sub(r"\s+", "", (value or "").lower())
        or "visibility:hidden" in re.sub(r"\s+", "", (value or "").lower())
        for value in styles
    )


def sanitize_plain_text(text: str, *, preserve_lines: bool = True) -> str:
    """Remove terminal controls and normalize untrusted provider text."""

    value = _ANSI_OSC_RE.sub("", text)
    value = _ANSI_STRING_RE.sub("", value)
    value = _ANSI_CSI_RE.sub("", value)
    value = _ANSI_SINGLE_RE.sub("", value)
    safe_chars: list[str] = []
    for character in value:
        if character == "\n" and preserve_lines:
            safe_chars.append(character)
            continue
        if character in {"\r", "\t", "\n"}:
            safe_chars.append(" ")
            continue
        if unicodedata.category(character) in {"Cc", "Cf", "Cs"}:
            continue
        safe_chars.append(character)
    normalized = "".join(safe_chars)
    if not preserve_lines:
        return " ".join(normalized.split())

    lines = [" ".join(line.split()) for line in normalized.split("\n")]
    result: list[str] = []
    for line in lines:
        if line:
            result.append(line)
        elif result and result[-1]:
            result.append("")
    while result and not result[-1]:
        result.pop()
    return "\n".join(result)


class _AnnouncementHTMLToTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []
        self.element_count = 0
        self.text_char_count = 0

    @property
    def suppressed(self) -> bool:
        return bool(self.stack and self.stack[-1][1])

    def _boundary(self) -> None:
        if self.parts and self.parts[-1] != "\n":
            self.parts.append("\n")

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, closes_itself: bool) -> None:
        normalized_tag = tag.lower()
        self.element_count += 1
        if self.element_count > MAX_ANNOUNCEMENT_ELEMENTS:
            raise AnnouncementSanitizationError("element_limit")
        if len(self.stack) >= MAX_ANNOUNCEMENT_NESTING:
            raise AnnouncementSanitizationError("nesting_limit")

        parent_suppressed = self.suppressed
        suppressed = (
            parent_suppressed or normalized_tag in _ACTIVE_OR_HIDDEN_TAGS or _is_hidden(attrs)
        )
        if not suppressed:
            if normalized_tag == "br":
                self._boundary()
            elif normalized_tag in _BLOCK_TAGS:
                self._boundary()
            if normalized_tag == "li":
                self.parts.append("- ")
        if not closes_itself and normalized_tag not in _VOID_TAGS:
            self.stack.append((normalized_tag, suppressed))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, closes_itself=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, closes_itself=True)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        matching_index = next(
            (
                index
                for index in range(len(self.stack) - 1, -1, -1)
                if self.stack[index][0] == normalized_tag
            ),
            None,
        )
        if matching_index is None:
            return
        _matched_tag, was_suppressed = self.stack[matching_index]
        del self.stack[matching_index:]
        if not was_suppressed and normalized_tag in _BLOCK_TAGS:
            self._boundary()

    def handle_data(self, data: str) -> None:
        if self.suppressed or not data:
            return
        self.text_char_count += len(data)
        if self.text_char_count > MAX_ANNOUNCEMENT_TEXT_CHARS:
            raise AnnouncementSanitizationError("text_limit")
        self.parts.append(data)


def sanitize_announcement_body(html: str) -> str | None:
    """Convert bounded, untrusted announcement HTML to safe plain text."""

    if len(html) > MAX_ANNOUNCEMENT_HTML_CHARS:
        raise AnnouncementSanitizationError("input_limit")
    parser = _AnnouncementHTMLToTextParser()
    try:
        parser.feed(html)
        parser.close()
    except (RecursionError, ValueError) as exc:
        if isinstance(exc, AnnouncementSanitizationError):
            raise
        raise AnnouncementSanitizationError("malformed_html") from None
    plain_text = sanitize_plain_text("".join(parser.parts))
    if len(plain_text) > MAX_ANNOUNCEMENT_TEXT_CHARS:
        raise AnnouncementSanitizationError("text_limit")
    return plain_text or None
