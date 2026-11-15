"""Application service: quiz metadata and the student's own attempt summaries (read-only).

Nothing here infers a completion state. A quiz with no attempts in the response is reported as
"no attempts returned", which is evidence about the response only: the provider may filter
attempts by role, and a quiz may be hidden from the student altogether.
"""

from __future__ import annotations

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.models.quiz import AttemptListing, QuizListing
from d2l_cli.providers.brightspace_quizzes import BrightspaceQuizProvider


class QuizService:
    def __init__(self, provider: BrightspaceQuizProvider) -> None:
        self._provider = provider

    def list_quizzes(self, course_id: str) -> QuizListing:
        listing = self._provider.list_quizzes(course_id)
        if any(quiz.course_id != course_id for quiz in listing.quizzes):
            raise ProviderEndpointChangedError(
                "Quiz service rejected mismatched course association"
            )
        return listing

    def list_attempts(self, course_id: str, quiz_id: str) -> AttemptListing:
        listing = self._provider.list_attempts(course_id, quiz_id)
        if listing.course_id != course_id or listing.quiz_id != quiz_id:
            raise ProviderEndpointChangedError("Quiz service rejected mismatched quiz association")
        return listing
