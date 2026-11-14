"""Application service: find the syllabus candidates of one course and fetch one of them.

Ranking is deliberately plain and explainable: a title or module that says "syllabus" is the only
strong signal, an acknowledgement quiz or form is penalised, and a candidate is *recommended* only
when exactly one fetchable candidate clearly leads. Anything less is returned as a list for the
person to choose from. Nothing here interprets the document; that is the application's job.
"""

from __future__ import annotations

import hashlib
import re

from d2l_cli.client.errors import MalformedResponseError, ProviderEndpointChangedError
from d2l_cli.docx_guard import DOCX_MEDIA_TYPE, ZIP_MAGIC, validate_docx
from d2l_cli.models.syllabus import (
    ContentTopic,
    SyllabusCandidate,
    SyllabusDocument,
    SyllabusSearch,
    TopicKind,
)
from d2l_cli.providers.brightspace_content import BrightspaceContentProvider

_WORD_RE = re.compile(r"[a-z0-9]+")
_SYLLABUS_WORDS = frozenset({"syllabus", "syllabi"})
_OUTLINE_PHRASES = ("course outline", "course information", "course overview")
_NOT_A_SYLLABUS_WORDS = frozenset(
    {
        "ack",
        "acknowledge",
        "acknowledgement",
        "acknowledgment",
        "agreement",
        "form",
        "quiz",
        "receipt",
        "signature",
        "survey",
        "test",
    }
)
_TITLE_SCORE = 100
_MODULE_SCORE = 60
_OUTLINE_SCORE = 30
_FILE_TYPE_BONUS = 10
_PENALTY = 70
_RECOMMEND_FLOOR = 70
_RECOMMEND_MARGIN = 20
_DOCUMENT_EXTENSIONS = {"pdf": "pdf", "txt": "txt", "docx": "docx"}
_MEDIA_TYPES = {"pdf": "application/pdf", "txt": "text/plain", "docx": DOCX_MEDIA_TYPE}
_OCTET_STREAM = "application/octet-stream"
_NOT_READABLE = "document was neither a PDF, a Word document nor plain text"
_NAME_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._ -]+")
_MAX_NAME_CHARS = 80
_TEXT_PROBE_BYTES = 4096


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.casefold())


def _is_fetchable(topic: ContentTopic) -> bool:
    if topic.kind is not TopicKind.FILE:
        return False
    return topic.file_extension is None or topic.file_extension in _DOCUMENT_EXTENSIONS


def score_topic(topic: ContentTopic) -> SyllabusCandidate | None:
    """A candidate for this topic, or ``None`` when nothing about it suggests a syllabus."""
    title_words = _words(topic.title)
    reasons: list[str] = []
    score = 0
    if _SYLLABUS_WORDS & set(title_words):
        score += _TITLE_SCORE
        reasons.append("title_mentions_syllabus")
    if any(_SYLLABUS_WORDS & set(_words(part)) for part in topic.module_path):
        score += _MODULE_SCORE
        reasons.append("module_mentions_syllabus")
    folded = " ".join(title_words)
    if score == 0 and any(phrase in folded for phrase in _OUTLINE_PHRASES):
        score += _OUTLINE_SCORE
        reasons.append("title_looks_like_an_outline")
    if score == 0:
        return None
    fetchable = _is_fetchable(topic)
    if fetchable:
        score += _FILE_TYPE_BONUS
        reasons.append("downloadable_document")
    elif topic.kind is TopicKind.FILE:
        reasons.append("unsupported_file_type")
    else:
        reasons.append("not_a_file")
    if _NOT_A_SYLLABUS_WORDS & set(title_words):
        score -= _PENALTY
        reasons.append("looks_like_an_acknowledgement")
    return SyllabusCandidate(topic=topic, score=score, reasons=tuple(reasons), fetchable=fetchable)


def rank_candidates(topics: tuple[ContentTopic, ...]) -> tuple[SyllabusCandidate, ...]:
    candidates = [c for c in (score_topic(t) for t in topics) if c is not None and c.score > 0]
    return tuple(sorted(candidates, key=lambda c: (-c.score, c.topic.title.casefold())))


def recommend(candidates: tuple[SyllabusCandidate, ...]) -> str | None:
    """The topic id of the one clearly leading fetchable candidate, otherwise ``None``."""
    fetchable = [c for c in candidates if c.fetchable]
    if not fetchable or fetchable[0].score < _RECOMMEND_FLOOR:
        return None
    if len(fetchable) > 1 and fetchable[0].score - fetchable[1].score < _RECOMMEND_MARGIN:
        return None
    return fetchable[0].topic.topic_id


def _mismatch() -> MalformedResponseError:
    return MalformedResponseError("document type did not match its declared type")


def _detect(content: bytes, declared: str) -> str:
    """The real type of the bytes (``pdf``, ``docx`` or ``txt``); refuse anything else.

    The type is decided by the bytes, never by a file name. A declared type may only agree with it;
    a generic ``application/octet-stream`` is accepted for a Word document because some servers
    send that. A Word document is additionally validated structurally (see ``docx_guard``).
    """
    if content.startswith(b"%PDF-"):
        if declared in ("text/plain", DOCX_MEDIA_TYPE):
            raise _mismatch()
        return "pdf"
    if content.startswith(ZIP_MAGIC):
        if declared not in (DOCX_MEDIA_TYPE, _OCTET_STREAM):
            raise _mismatch()
        validate_docx(content)
        return "docx"
    if declared in ("application/pdf", DOCX_MEDIA_TYPE):
        raise _mismatch()
    if b"\x00" in content[:_TEXT_PROBE_BYTES]:
        raise MalformedResponseError(_NOT_READABLE)
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        raise MalformedResponseError(_NOT_READABLE) from None
    return "txt"


def _file_name(title: str, kind: str) -> str:
    stem = _NAME_UNSAFE_RE.sub("", title).strip(" .-_")[:_MAX_NAME_CHARS].strip(" .-_")
    return f"{stem or 'syllabus'}.{kind}"


class SyllabusService:
    def __init__(self, provider: BrightspaceContentProvider) -> None:
        self._provider = provider

    def find(self, course_id: str) -> SyllabusSearch:
        topics = self._provider.list_topics(course_id)
        if any(topic.course_id != course_id for topic in topics):
            raise ProviderEndpointChangedError(
                "Course content service rejected mismatched course association"
            )
        candidates = rank_candidates(topics)
        return SyllabusSearch(
            course_id=course_id,
            api_version=self._provider.api_version(),
            candidates=candidates,
            recommended_topic_id=recommend(candidates),
            topic_count=len(topics),
        )

    def fetch(self, course_id: str, topic_id: str) -> SyllabusDocument:
        """Download one file, only if the course's own contents list it as a visible document."""
        topics = self._provider.list_topics(course_id)
        listed = next((t for t in topics if t.topic_id == topic_id), None)
        if listed is None or not _is_fetchable(listed):
            raise ValueError("topic is not a downloadable document of this course")
        content, declared = self._provider.download_topic_file(course_id, topic_id)
        kind = _detect(content, declared)
        return SyllabusDocument(
            course_id=course_id,
            topic_id=topic_id,
            title=listed.title,
            module_path=listed.module_path,
            file_name=_file_name(listed.title, kind),
            media_type=_MEDIA_TYPES[kind],
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            api_version=self._provider.api_version(),
        )
