"""Application-level course service.

Sits between the CLI and the Brightspace provider adapter. Knows nothing
about raw provider field names -- only the normalized ``Course`` model and
the provider's narrow ``list_courses()`` contract.
"""

from __future__ import annotations

from d2l_cli.client.errors import UnsupportedPaginationStateError
from d2l_cli.models.course import Course
from d2l_cli.providers.brightspace_courses import BrightspaceCourseProvider


class CourseService:
    def __init__(self, provider: BrightspaceCourseProvider) -> None:
        self._provider = provider

    def list_courses(self) -> list[Course]:
        page = self._provider.list_courses()
        if page.bookmark is not None:
            # Phase 0.5 reconnaissance observed `Bookmark` only as null
            # (single page); the request-parameter mechanism for fetching a
            # subsequent page was never established (see docs/PROVIDER_NOTES.md,
            # "What remains unknown"). Failing loudly here means a
            # truncated course list is never mistaken for a complete one.
            raise UnsupportedPaginationStateError(
                "Brightspace indicated more courses exist beyond the first "
                "page (non-null Bookmark), but subsequent-page request "
                "semantics were not established during Phase 0.5 "
                "reconnaissance; refusing to silently return a truncated "
                "course list"
            )
        return list(page.courses)
