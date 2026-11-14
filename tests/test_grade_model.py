from __future__ import annotations

import pytest

from d2l_cli.models.grade import (
    FeedbackAvailability,
    GradeCompleteness,
    GradeItem,
    GradeListPage,
)


def test_grade_item_preserves_display_strings_without_numeric_semantics() -> None:
    item = GradeItem(
        course_id="620000",
        label="Fictional Assessment",
        displayed_points="7.25 / 9.5",
        displayed_grade="Pending Review",
        displayed_weight="3 / 4",
        feedback_availability=FeedbackAvailability.UNKNOWN,
    )

    assert item.displayed_points == "7.25 / 9.5"
    assert item.displayed_grade == "Pending Review"
    assert item.displayed_weight == "3 / 4"
    assert not hasattr(item, "earned_points")
    assert not hasattr(item, "possible_points")
    assert not hasattr(item, "percentage")
    assert not hasattr(item, "state")
    assert not hasattr(item, "feedback")


def test_grade_page_rejects_invalid_structural_metadata() -> None:
    with pytest.raises(ValueError, match="category_row_count"):
        GradeListPage((), -1, 4, False)
    with pytest.raises(ValueError, match="header_effective_width"):
        GradeListPage((), 0, 0, False)


def test_grade_page_completeness_is_explicitly_unknown() -> None:
    page = GradeListPage((), 0, 4, False)

    assert page.completeness is GradeCompleteness.UNKNOWN
    assert list(GradeCompleteness) == [GradeCompleteness.UNKNOWN]
