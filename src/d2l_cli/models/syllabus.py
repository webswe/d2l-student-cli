"""Normalized, read-only models for syllabus discovery and retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TopicKind(StrEnum):
    """What a course-content topic is, as far as retrieval is concerned."""

    FILE = "file"  # a course file the student can download
    OTHER = "other"  # a link, quiz, discussion, ... : listed for context, never fetched


@dataclass(frozen=True, slots=True)
class ContentTopic:
    """One visible topic of the course table of contents, with its module trail."""

    course_id: str
    topic_id: str
    title: str
    module_path: tuple[str, ...]
    kind: TopicKind
    file_extension: str | None
    last_modified: str | None


@dataclass(frozen=True, slots=True)
class SyllabusCandidate:
    """A topic that may be the course syllabus, with the reasons it was ranked.

    ``reasons`` are fixed codes (never provider text), so a caller can show why a candidate was
    proposed without trusting free text.
    """

    topic: ContentTopic
    score: int
    reasons: tuple[str, ...]
    fetchable: bool


@dataclass(frozen=True, slots=True)
class SyllabusSearch:
    """The ranked candidates for one course and which one, if any, is unambiguous."""

    course_id: str
    api_version: str
    candidates: tuple[SyllabusCandidate, ...]
    recommended_topic_id: str | None
    topic_count: int


@dataclass(frozen=True, slots=True)
class SyllabusDocument:
    """A retrieved document: validated bytes plus where they came from."""

    course_id: str
    topic_id: str
    title: str
    module_path: tuple[str, ...]
    file_name: str
    media_type: str
    content: bytes
    sha256: str
    api_version: str
