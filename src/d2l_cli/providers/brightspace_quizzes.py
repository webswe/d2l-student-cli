"""GET-only provider for the quizzes a student can see and the student's own attempt summaries.

Routes (documented Valence ``le`` quiz API, LE version 1.82 or later):

* ``GET /d2l/api/le/{version}/{orgUnitId}/quizzes/`` (scope ``quizzing:quizzes:read``)
* ``GET /d2l/api/le/{version}/{orgUnitId}/quizzes/{quizId}/attempts/?userId={id}`` (scope
  ``quizzing:attempts:read``)

Deliberately NOT used: the ``questions`` routes (they expose answer options), attempt feedback
routes, and any route that starts, answers or submits a quiz.

Ownership: the unfiltered attempts route returns attempts "for the quiz generally", so a response
that merely lacks conflicting user ids proves nothing. Attempts are therefore requested only
after the signed-in user's id has been verified (``BrightspaceIdentityProvider``), with that id as
the documented ``userId`` filter, and every returned record must itself carry exactly that
``UserId``. There is no fallback to an unfiltered request. ``IsPublished`` is documented as "the
attempt feedback is available to the student" and is reported as ``feedback_published`` only; the
undocumented ``Score`` is never exposed. Not confirmed against a live tenant; see
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
from d2l_cli.providers.brightspace_identity import BrightspaceIdentityProvider

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


def _score_is_valid(value: object) -> None:
    """``Score`` is undocumented (no meaning, no release rule). It is checked and never exposed."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise v.changed(_LABEL, "SCORE")
    if value != value or value in (float("inf"), float("-inf")):  # NaN / infinity
        raise v.changed(_LABEL, "SCORE")


def parse_attempt(node: dict[str, Any], *, quiz_id: str, user_id: str) -> QuizAttempt:
    """One attempt, only if it provably belongs to ``user_id`` and to ``quiz_id``.

    ``user_id`` is the id verified for the current session. A missing or different ``UserId``,
    or a different ``QuizId``, rejects the whole response: ownership is never assumed.
    """
    if v.id_of(node.get("QuizId"), _LABEL, "ATTEMPT_QUIZ") != quiz_id:
        raise v.changed(_LABEL, "QUIZ_MISMATCH")
    if node.get("UserId") is None:
        raise v.changed(_LABEL, "ATTEMPT_OWNER_MISSING")
    if v.id_of(node["UserId"], _LABEL, "ATTEMPT_OWNER") != user_id:
        raise v.changed(_LABEL, "ATTEMPT_OWNER_MISMATCH")
    started = v.utc_datetime(node.get("Started"), _LABEL, "STARTED")
    completed = v.utc_datetime(node.get("Completed"), _LABEL, "COMPLETED")
    published = _flag(node, "IsPublished", "PUBLISHED_FLAG")
    _score_is_valid(node.get("Score"))
    if completed is not None:
        status = AttemptStatus.COMPLETED
    elif started is not None:
        status = AttemptStatus.IN_PROGRESS
    else:
        status = AttemptStatus.UNKNOWN
    return QuizAttempt(
        attempt_id=v.id_of(node.get("AttemptId"), _LABEL, "ATTEMPT_ID"),
        quiz_id=quiz_id,
        attempt_number=_bounded_int(node.get("AttemptNumber"), "ATTEMPT_NUMBER", maximum=10_000),
        started=started,
        completed=completed,
        status=status,
        attempt_completed=completed is not None,
        feedback_published=published,
    )


def parse_attempts(
    payload: object, *, course_id: str, quiz_id: str, user_id: str
) -> AttemptListing:
    items, has_next = v.object_page(payload, _LABEL, max_items=MAX_ATTEMPTS)
    attempts: list[QuizAttempt] = []
    seen: set[str] = set()
    for item in items:
        attempt = parse_attempt(item, quiz_id=quiz_id, user_id=user_id)
        if attempt.attempt_id in seen:
            raise v.changed(_LABEL, "DUPLICATE_ATTEMPT")
        seen.add(attempt.attempt_id)
        attempts.append(attempt)
    return AttemptListing(
        course_id=course_id, quiz_id=quiz_id, attempts=tuple(attempts), has_more=has_next
    )


class BrightspaceQuizProvider:
    """Read quiz metadata and the verified signed-in student's own attempt summaries."""

    def __init__(
        self, client: D2LHttpClient, identity: BrightspaceIdentityProvider | None = None
    ) -> None:
        self._client = client
        self._identity = identity or BrightspaceIdentityProvider(client)
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
        """Attempts of the verified current user only, or an error; never an unfiltered list.

        Order is fixed: validate ids, verify who is signed in, then request attempts filtered to
        that user id, then check every returned record against that same id. If the identity
        cannot be verified no attempts request is made at all.
        """
        course = v.require_id(course_id, "course_id")
        quiz = v.require_id(quiz_id, "quiz_id")
        user_id = self._identity.current_user_id()
        path = f"/d2l/api/le/{self.api_version()}/{course}/quizzes/{quiz}/attempts/"
        payload = self._client.get(path, params={"userId": user_id}).json_body
        return parse_attempts(payload, course_id=course, quiz_id=quiz, user_id=user_id)
