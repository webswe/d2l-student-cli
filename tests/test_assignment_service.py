from __future__ import annotations

import pytest

from d2l_cli.assignment_service import AssignmentService
from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.models.assignment import Assignment
from d2l_cli.models.assignment_completeness import (
    AssignmentCompleteness,
    AssignmentCompletenessStatus,
    AssignmentListPage,
)

_ASSIGNMENT = Assignment(
    id="90210",
    course_id="620000",
    name="Homework 1",
    submission_status="Submitted",
    score="18 / 20",
)


class FakeProvider:
    def __init__(
        self, assignments: tuple[Assignment, ...] = (), *, raises: Exception | None = None
    ) -> None:
        self._assignments = assignments
        self._raises = raises
        self.requested_course_ids: list[str] = []

    def list_assignments(self, course_id: str) -> tuple[Assignment, ...]:
        self.requested_course_ids.append(course_id)
        if self._raises is not None:
            raise self._raises
        return self._assignments

    def get_assignment_list(self, course_id: str) -> AssignmentListPage:
        self.requested_course_ids.append(course_id)
        if self._raises is not None:
            raise self._raises
        return AssignmentListPage(
            assignments=self._assignments,
            completeness=AssignmentCompleteness(
                status=AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY,
                observed_row_count=len(self._assignments),
                page_size_ceiling=len(self._assignments) + 1,
            ),
        )


def test_list_assignments_passes_through_normalized_data() -> None:
    provider = FakeProvider((_ASSIGNMENT,))
    service = AssignmentService(provider)  # type: ignore[arg-type]
    assert service.list_assignments("620000") == [_ASSIGNMENT]


def test_list_assignments_forwards_course_id_to_provider() -> None:
    provider = FakeProvider(())
    service = AssignmentService(provider)  # type: ignore[arg-type]
    service.list_assignments("620000")
    assert provider.requested_course_ids == ["620000"]


def test_list_assignments_returns_empty_list_for_genuinely_empty_course() -> None:
    provider = FakeProvider(())
    service = AssignmentService(provider)  # type: ignore[arg-type]
    assert service.list_assignments("620000") == []


def test_list_assignments_propagates_provider_errors() -> None:
    provider = FakeProvider(raises=ProviderEndpointChangedError("structure changed"))
    service = AssignmentService(provider)  # type: ignore[arg-type]
    with pytest.raises(ProviderEndpointChangedError):
        service.list_assignments("620000")


def test_get_assignment_list_preserves_completeness_metadata() -> None:
    provider = FakeProvider((_ASSIGNMENT,))
    result = AssignmentService(provider).get_assignment_list("620000")  # type: ignore[arg-type]

    assert result.assignments == (_ASSIGNMENT,)
    assert result.completeness.status == AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY
