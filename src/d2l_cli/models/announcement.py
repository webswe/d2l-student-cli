"""Normalized, read-only models for visible Brightspace announcements."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AnnouncementCompleteness(StrEnum):
    """Evidence-bounded completeness for one returned News listing."""

    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Announcement:
    """One visible announcement from a validated course-scoped listing row."""

    course_id: str
    announcement_id: str
    title: str
    body: str | None
    detail_reference: str


@dataclass(frozen=True, slots=True)
class AnnouncementListPage:
    """Ordered visible announcements plus conservative completeness metadata."""

    items: tuple[Announcement, ...]
    completeness: AnnouncementCompleteness = AnnouncementCompleteness.UNKNOWN
