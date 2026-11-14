from __future__ import annotations

import pytest

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.grade_service import GradeService
from d2l_cli.models.grade import (
    FeedbackAvailability,
    GradeCompleteness,
    GradeItem,
    GradeListPage,
)

_ITEM = GradeItem(
    course_id="620000",
    label="Fictional Quiz",
    displayed_points="7 / 9",
    displayed_grade="77.8%",
    displayed_weight=None,
    feedback_availability=FeedbackAvailability.NONE,
)


class _FakeProvider:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises
        self.requested_course_ids: list[str] = []

    def list_grade_items(self, course_id: str) -> tuple[GradeItem, ...]:
        self.requested_course_ids.append(course_id)
        if self.raises is not None:
            raise self.raises
        return (_ITEM,)

    def get_grade_list(self, course_id: str) -> GradeListPage:
        self.requested_course_ids.append(course_id)
        if self.raises is not None:
            raise self.raises
        return GradeListPage((_ITEM,), 2, 4, False)


def test_service_returns_items_in_provider_order() -> None:
    provider = _FakeProvider()
    service = GradeService(provider)  # type: ignore[arg-type]
    assert service.list_grade_items("620000") == [_ITEM]
    assert provider.requested_course_ids == ["620000"]


def test_service_preserves_structural_metadata() -> None:
    page = GradeService(_FakeProvider()).get_grade_list("620000")  # type: ignore[arg-type]
    assert page.items == (_ITEM,)
    assert page.category_row_count == 2
    assert page.header_effective_width == 4
    assert page.completeness is GradeCompleteness.UNKNOWN


def test_service_propagates_provider_errors() -> None:
    service = GradeService(  # type: ignore[arg-type]
        _FakeProvider(raises=ProviderEndpointChangedError("structure changed"))
    )
    with pytest.raises(ProviderEndpointChangedError):
        service.list_grade_items("620000")
