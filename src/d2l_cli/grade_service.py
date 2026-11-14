"""Application service for read-only visible Brightspace grade items."""

from __future__ import annotations

from d2l_cli.models.grade import GradeItem, GradeListPage
from d2l_cli.providers.brightspace_grades import BrightspaceGradeProvider


class GradeService:
    def __init__(self, provider: BrightspaceGradeProvider) -> None:
        self._provider = provider

    def list_grade_items(self, course_id: str) -> list[GradeItem]:
        """Return visible items in provider order without grade calculation."""
        return list(self._provider.list_grade_items(course_id))

    def get_grade_list(self, course_id: str) -> GradeListPage:
        """Return items plus value-free structural validation metadata."""
        return self._provider.get_grade_list(course_id)
