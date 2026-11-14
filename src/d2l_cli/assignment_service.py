"""Application-level assignment service.

Sits between the CLI and the Brightspace assignment provider adapter.
Knows nothing about raw HTML/provider details -- only the normalized
``Assignment`` model and the provider's narrow Assignment-list contract.
The provider cannot fetch another page under the GET-only policy, but it
does return evidence about whether the rendered row count reached the
selected page-size ceiling.
"""

from __future__ import annotations

from d2l_cli.models.assignment import Assignment
from d2l_cli.models.assignment_completeness import AssignmentListPage
from d2l_cli.providers.brightspace_assignments import BrightspaceAssignmentProvider


class AssignmentService:
    def __init__(self, provider: BrightspaceAssignmentProvider) -> None:
        self._provider = provider

    def list_assignments(self, course_id: str) -> list[Assignment]:
        return list(self._provider.list_assignments(course_id))

    def get_assignment_list(self, course_id: str) -> AssignmentListPage:
        """Return assignments together with source-page boundary evidence."""
        return self._provider.get_assignment_list(course_id)
