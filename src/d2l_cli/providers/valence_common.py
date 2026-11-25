"""Shared, fail-closed parsing helpers for the read-only Valence ``le`` providers.

Calendar, quizzes and discussions all use the documented Brightspace Valence *Learning
Environment* API with the signed-in student's own session. Every helper here either returns a
validated, bounded value or raises ``ProviderEndpointChangedError``; nothing is guessed, repaired
or silently dropped. The route shapes were taken from the published Valence reference and have
NOT been confirmed against a live tenant (see docs/ACTIVITIES.md).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from d2l_cli.announcement_sanitizer import (
    AnnouncementSanitizationError,
    sanitize_announcement_body,
    sanitize_plain_text,
)
from d2l_cli.client.errors import ProviderEndpointChangedError

VERSIONS_PATH = "/d2l/api/versions/"
PRODUCT_CODE = "le"
#: The calendar, quiz and discussion routes used here are documented from LE API version 1.82.
#: Earlier versions are obsolete for them, so selecting one would only guarantee a 404. The
#: ceiling stops a future redesign being consumed silently.
MIN_MINOR = 82
MAX_MINOR = 120
_VERSION_RE = re.compile(r"1\.([0-9]{1,3})")
_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z")
_LOCAL_DATETIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(T\d{2}:\d{2}:\d{2}(\.\d{1,9})?)?")
MAX_TITLE_CHARS = 500
MAX_TEXT_CHARS = 20_000


def changed(label: str, code: str) -> ProviderEndpointChangedError:
    """The one error for any response that deviates from the documented shape."""
    return ProviderEndpointChangedError(f"{label} structure is unsupported ({code.upper()})")


def is_numeric_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value.isascii()
        and value.isdecimal()
        and len(value) <= 20
    )


def require_id(value: object, name: str) -> str:
    """Reject anything that is not a plain numeric id *before* a request is built."""
    if not is_numeric_id(value):
        raise ValueError(f"{name} must be a non-empty numeric Brightspace id")
    assert isinstance(value, str)
    return value


def id_of(value: object, label: str, code: str) -> str:
    """A provider id (JSON integer or numeric string) as a string."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise changed(label, code)
    text = str(value)
    if not is_numeric_id(text):
        raise changed(label, code)
    return text


def select_le_version(payload: object, label: str) -> str:
    """The newest documented ``le`` version inside ``1.MIN_MINOR..1.MAX_MINOR``, or fail closed."""
    return select_product_version(payload, label, PRODUCT_CODE, MIN_MINOR, MAX_MINOR)


def select_product_version(
    payload: object, label: str, product: str, minimum: int, maximum: int
) -> str:
    """The newest version of ``product`` listed between ``1.minimum`` and ``1.maximum``.

    Product version numbers are independent (the ``lp`` and ``le`` families do not share a
    numbering), so each caller states its own documented floor.
    """
    if not isinstance(payload, list):
        raise changed(label, "VERSIONS_SHAPE")
    for entry in payload:
        if not isinstance(entry, dict) or entry.get("ProductCode") != product:
            continue
        supported = entry.get("SupportedVersions")
        if not isinstance(supported, list) or not all(isinstance(v, str) for v in supported):
            raise changed(label, "VERSIONS_SHAPE")
        usable: list[int] = []
        for version in supported:
            match = _VERSION_RE.fullmatch(version)
            if match and minimum <= int(match.group(1)) <= maximum:
                usable.append(int(match.group(1)))
        if not usable:
            raise changed(label, "VERSION_UNSUPPORTED")
        return f"1.{max(usable)}"
    raise changed(label, "VERSION_UNSUPPORTED")


def minor_of(version: str) -> int:
    """The minor number of a ``1.NN`` version string chosen by ``select_product_version``."""
    return int(version.split(".", 1)[1])


def object_page(
    payload: object, label: str, *, max_items: int
) -> tuple[list[dict[str, Any]], bool]:
    """Items of a list response and whether the provider says more pages exist.

    Accepts a bare JSON array or the documented ``ObjectListPage`` (``Objects`` plus ``Next``).
    A non-null ``Next`` is reported, never followed: following it would need an absolute URL, and
    the HTTP client only issues relative same-origin requests.
    """
    has_next = False
    items: object = payload
    if isinstance(payload, dict):
        items = payload.get("Objects")
        next_value = payload.get("Next")
        if next_value is not None and not isinstance(next_value, str):
            raise changed(label, "PAGE_SHAPE")
        has_next = bool(next_value)
    if not isinstance(items, list) or len(items) > max_items:
        raise changed(label, "PAGE_SHAPE" if not isinstance(items, list) else "ITEM_LIMIT")
    if not all(isinstance(item, dict) for item in items):
        raise changed(label, "ITEM_SHAPE")
    return items, has_next


def clean_text(value: object, label: str, code: str, *, limit: int = MAX_TITLE_CHARS) -> str:
    if not isinstance(value, str):
        raise changed(label, code)
    return sanitize_plain_text(value, preserve_lines=False)[:limit]


def optional_text(
    value: object, label: str, code: str, *, limit: int = MAX_TITLE_CHARS
) -> str | None:
    if value is None:
        return None
    text = clean_text(value, label, code, limit=limit)
    return text or None


def rich_text(
    value: object, label: str, code: str, *, limit: int | None = MAX_TEXT_CHARS
) -> str | None:
    """Plain text from a provider description: an HTML string or a ``{Html|Text: ...}`` object.

    Cut at ``limit`` characters (``None`` = never cut). Callers that must tell the reader about a
    cut use ``rich_text_bounded``.
    """
    if value is None:
        return None
    source: object = value
    if isinstance(value, dict):
        source = value.get("Html") if value.get("Html") is not None else value.get("Text")
        if isinstance(source, dict):  # some builds nest one more RichText level
            source = source.get("Html") if source.get("Html") is not None else source.get("Text")
    if source is None:
        return None
    if not isinstance(source, str):
        raise changed(label, code)
    try:
        text = sanitize_announcement_body(source)
    except AnnouncementSanitizationError:
        raise changed(label, code) from None
    if not text:
        return None
    return text if limit is None else text[:limit]


def rich_text_bounded(
    value: object, label: str, code: str, *, limit: int = MAX_TEXT_CHARS
) -> tuple[str | None, bool]:
    """Plain text from a provider body plus whether it was cut at ``limit`` characters."""
    text = rich_text(value, label, code, limit=None)
    if text is None or len(text) <= limit:
        return text, False
    return text[:limit], True


def utc_datetime(value: object, label: str, code: str) -> str | None:
    """A UTC instant exactly as documented (``...Z``), or ``None`` when the provider sent null."""
    if value is None:
        return None
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise changed(label, code)
    try:
        parse_utc(value)  # rejects impossible dates such as month 13
    except ValueError:
        raise changed(label, code) from None
    return value


def local_day(value: object, label: str, code: str) -> str | None:
    """The ``YYYY-MM-DD`` calendar day of a local (zone-less) date or datetime, or ``None``."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise changed(label, code)
    match = _LOCAL_DATETIME_RE.fullmatch(value)
    if not match:
        raise changed(label, code)
    try:
        datetime.strptime(match.group(1), "%Y-%m-%d")
    except ValueError:
        raise changed(label, code) from None
    return match.group(1)


def parse_utc(value: str) -> datetime:
    """An aware UTC ``datetime`` from a validated ``...Z`` string (fraction truncated to micro)."""
    match = re.fullmatch(r"(.*?)(?:\.(\d{1,9}))?Z", value)
    if not match:
        raise ValueError("not a UTC instant")
    base = match.group(1)
    fraction = (match.group(2) or "")[:6].ljust(6, "0")
    return datetime.fromisoformat(f"{base}.{fraction}+00:00")


def relative_reference(value: object) -> str | None:
    """A provider navigation link as a same-origin relative path, or ``None``.

    Reported as data only; the CLI never requests it.
    """
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return None
    path = value.split("?", 1)[0]
    if any(ch in value for ch in "\\\n\r") or len(value) > 500 or "@" in path:
        return None
    return value
