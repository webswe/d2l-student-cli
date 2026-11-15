"""GET-only provider for the quizzes a student can see and the student's own attempt summaries.

Routes (documented Valence ``le`` quiz API, LE version 1.82 or later):

* ``GET /d2l/api/le/{version}/{orgUnitId}/quizzes/`` (scope ``quizzing:quizzes:read``)
* ``GET /d2l/api/le/{version}/{orgUnitId}/quizzes/{quizId}/attempts/`` (scope
  ``quizzing:attempts:read``)

Deliberately NOT used: the ``questions`` routes (they expose answer options), attempt feedback,
and any route that starts, answers or submits a quiz. The documentation does not say whether a
student role may call the attempts route, so a permission denial is surfaced as an error and an
empty listing is never read as "no attempts". Attempts are not filtered by ``userId`` (the CLI does
not know the user id); instead a response that mixes more than one user is refused, so another
person's attempts can never be shown. Not confirmed against a live tenant; see
docs/ACTIVITIES.md.
"""

from __future__ import annotations

from typing import Any

from d2l_cli.client.http import D2LHttpClient
from d2l_cli.models.quiz import (
    AttemptListing,
    AttemptStatus,
    Quiz,
    QuizAttempt,
    QuizListing,
)
from d2l_cli.providers import valence_common as v

_LABEL = "Quiz"
MAX_QUIZZES = 2_000
MAX_ATTEMPTS = 1_000
_MAX_MINUTES = 100_000
_MAX_ATTEMPTS_ALLOWED = 10_000


def _flag(node: dict[str, Any], key: str, code: str) -> bool | None:
    value = node.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise v.changed(_LABEL, code)
    return value


def _bounded_int(value: object, code: str, *, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise v.changed(_LABEL, code)
    return value


def _description(value: object) -> str | None:
    if isinstance(value, dict) and value.get("IsDisplayed") is False:
        return None  # the instructor chose not to display it to students
    return v.rich_text(value, _LABEL, "DESCRIPTION")


def parse_quiz(node: dict[str, Any], *, course_id: str) -> Quiz:
    limit = node.get("SubmissionTimeLimit")
    if limit is not None and not isinstance(limit, dict):
        raise v.changed(_LABEL, "TIME_LIMIT_SHAPE")
    allowed = node.get("AttemptsAllowed")
    if allowed is not None and not isinstance(allowed, dict):
        raise v.changed(_LABEL, "ATTEMPTS_SHAPE")
    enforced = _flag(limit, "IsEnforced", "TIME_LIMIT_FLAG") if limit is not None else None
    minutes = (
        _bounded_int(limit.get("TimeLimitValue"), "TIME_LIMIT_VALUE", maximum=_MAX_MINUTES)
        if limit is not None
        else None
    )
    unlimited = _flag(allowed, "IsUnlimited", "ATTEMPTS_FLAG") if allowed is not None else None
    count = (
        _bounded_int(
            allowed.get("NumberOfAttemptsAllowed"), "ATTEMPTS_VALUE", maximum=_MAX_ATTEMPTS_ALLOWED
        )
        if allowed is not None
        else None
    )
    return Quiz(
        quiz_id=v.id_of(node.get("QuizId"), _LABEL, "QUIZ_ID"),
        course_id=course_id,
        name=v.clean_text(node.get("Name"), _LABEL, "NAME"),
        description=_description(node.get("Description")),
        active=_flag(node, "IsActive", "ACTIVE_FLAG"),
        start_date=v.utc_datetime(node.get("StartDate"), _LABEL, "START"),
        end_date=v.utc_datetime(node.get("EndDate"), _LABEL, "END"),
        due_date=v.utc_datetime(node.get("DueDate"), _LABEL, "DUE"),
        # A time limit that is not enforced is reported as no limit rather than as a number.
        time_limit_minutes=minutes if enforced else None,
        time_limit_enforced=enforced,
        attempts_allowed=None if unlimited else count,
        attempts_unlimited=unlimited,
    )


def parse_quizzes(payload: object, *, course_id: str) -> QuizListing:
    items, has_next = v.object_page(payload, _LABEL, max_items=MAX_QUIZZES)
    quizzes: list[Quiz] = []
    seen: set[str] = set()
    for item in items:
        quiz = parse_quiz(item, course_id=course_id)
        if quiz.quiz_id in seen:
            raise v.changed(_LABEL, "DUPLICATE_QUIZ")
        seen.add(quiz.quiz_id)
        quizzes.append(quiz)
    return QuizListing(course_id=course_id, quizzes=tuple(quizzes), has_more=has_next)


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise v.changed(_LABEL, "SCORE")
    if value != value or value in (float("inf"), float("-inf")):  # NaN / infinity
        raise v.changed(_LABEL, "SCORE")
    return float(value)


def parse_attempt(node: dict[str, Any], *, quiz_id: str) -> tuple[QuizAttempt, str | None]:
    """The attempt summary plus its (never displayed) user id, used only to refuse mixed users."""
    if v.id_of(node.get("QuizId"), _LABEL, "ATTEMPT_QUIZ") != quiz_id:
        raise v.changed(_LABEL, "QUIZ_MISMATCH")
    user = node.get("UserId")
    user_id = None if user is None else v.id_of(user, _LABEL, "USER_ID")
    started = v.utc_datetime(node.get("Started"), _LABEL, "STARTED")
    completed = v.utc_datetime(node.get("Completed"), _LABEL, "COMPLETED")
    released = _flag(node, "IsPublished", "PUBLISHED_FLAG")
    score = _number(node.get("Score"))
    if completed is not None:
        status = AttemptStatus.COMPLETED
    elif started is not None:
        status = AttemptStatus.IN_PROGRESS
    else:
        status = AttemptStatus.UNKNOWN
    return (
        QuizAttempt(
            attempt_id=v.id_of(node.get("AttemptId"), _LABEL, "ATTEMPT_ID"),
            quiz_id=quiz_id,
            attempt_number=_bounded_int(
                node.get("AttemptNumber"), "ATTEMPT_NUMBER", maximum=10_000
            ),
            started=started,
            completed=completed,
            status=status,
            # A score is shown only when the provider marks the attempt released.
            score=score if released is True else None,
            score_released=released,
        ),
        user_id,
    )


def parse_attempts(payload: object, *, course_id: str, quiz_id: str) -> AttemptListing:
    items, has_next = v.object_page(payload, _LABEL, max_items=MAX_ATTEMPTS)
    attempts: list[QuizAttempt] = []
    users: set[str] = set()
    seen: set[str] = set()
    for item in items:
        attempt, user_id = parse_attempt(item, quiz_id=quiz_id)
        if attempt.attempt_id in seen:
            raise v.changed(_LABEL, "DUPLICATE_ATTEMPT")
        seen.add(attempt.attempt_id)
        if user_id is not None:
            users.add(user_id)
        attempts.append(attempt)
    if len(users) > 1:
        # We cannot tell which user is the signed-in student, so show none of them.
        raise v.changed(_LABEL, "OTHER_USERS_ATTEMPTS")
    return AttemptListing(
        course_id=course_id, quiz_id=quiz_id, attempts=tuple(attempts), has_more=has_next
    )


class BrightspaceQuizProvider:
    """Read quiz metadata and the signed-in student's own attempt summaries."""

    def __init__(self, client: D2LHttpClient) -> None:
        self._client = client
        self._version: str | None = None

    def api_version(self) -> str:
        if self._version is None:
            response = self._client.get(v.VERSIONS_PATH)
            self._version = v.select_le_version(response.json_body, _LABEL)
        return self._version

    def list_quizzes(self, course_id: str) -> QuizListing:
        course = v.require_id(course_id, "course_id")
        path = f"/d2l/api/le/{self.api_version()}/{course}/quizzes/"
        return parse_quizzes(self._client.get(path).json_body, course_id=course)

    def list_attempts(self, course_id: str, quiz_id: str) -> AttemptListing:
        course = v.require_id(course_id, "course_id")
        quiz = v.require_id(quiz_id, "quiz_id")
        path = f"/d2l/api/le/{self.api_version()}/{course}/quizzes/{quiz}/attempts/"
        return parse_attempts(self._client.get(path).json_body, course_id=course, quiz_id=quiz)
