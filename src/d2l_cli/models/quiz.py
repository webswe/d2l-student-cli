"""Normalized, read-only quiz models.

Only metadata the student's role is shown and the student's own attempt summaries. Questions,
answer options, answer keys, attempt feedback and other users' attempts are never modelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Quiz:
    """One quiz as the provider reports it. ``None`` always means *not reported*, never zero."""

    quiz_id: str
    course_id: str
    name: str
    description: str | None
    active: bool | None
    start_date: str | None
    end_date: str | None
    due_date: str | None
    time_limit_minutes: int | None
    time_limit_enforced: bool | None
    attempts_allowed: int | None
    attempts_unlimited: bool | None


@dataclass(frozen=True, slots=True)
class QuizListing:
    """The quizzes the provider chose to show. An empty listing is not proof of "no quizzes"."""

    course_id: str
    quizzes: tuple[Quiz, ...]
    has_more: bool

    @property
    def complete(self) -> bool:
        return not self.has_more


class AttemptStatus(StrEnum):
    """What the attempt record itself shows, never an inference about the quiz."""

    COMPLETED = "completed"  # the provider gave a completion time
    IN_PROGRESS = "in_progress"  # started, no completion time
    UNKNOWN = "unknown"  # neither time was reported


@dataclass(frozen=True, slots=True)
class QuizAttempt:
    """One attempt summary. ``score`` is present only when the provider marks it released."""

    attempt_id: str
    quiz_id: str
    attempt_number: int | None
    started: str | None
    completed: str | None
    status: AttemptStatus
    score: float | None
    score_released: bool | None


@dataclass(frozen=True, slots=True)
class AttemptListing:
    course_id: str
    quiz_id: str
    attempts: tuple[QuizAttempt, ...]
    has_more: bool

    @property
    def complete(self) -> bool:
        return not self.has_more
