"""Normalized, display-only models for visible Brightspace grade items.

These models deliberately preserve provider-rendered strings. A points value
such as a fraction is not split into earned/possible components; a grade is
not converted to a percentage; and no course-level result is calculated.
``None`` means the corresponding table cell was blank. It does not imply an
academic state such as ungraded, missing, hidden, or unreleased.

Feedback content is intentionally excluded. Reconnaissance established enough
structure to distinguish a genuinely empty cell from a non-empty cell, while
an empty-text cell containing provider elements remains ambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FeedbackAvailability(StrEnum):
    NONE = "none"
    PRESENT = "present"
    UNKNOWN = "unknown"


class GradeCompleteness(StrEnum):
    """Evidence-bounded completeness for one returned grade-list page."""

    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class GradeItem:
    course_id: str
    label: str
    displayed_points: str | None
    displayed_grade: str | None
    displayed_weight: str | None
    feedback_availability: FeedbackAvailability


@dataclass(frozen=True, slots=True)
class GradeListPage:
    """Parsed visible items plus page-level structural/completeness metadata."""

    items: tuple[GradeItem, ...]
    category_row_count: int
    header_effective_width: int
    has_weight_column: bool
    completeness: GradeCompleteness = GradeCompleteness.UNKNOWN

    def __post_init__(self) -> None:
        if self.category_row_count < 0:
            raise ValueError("category_row_count must not be negative")
        if self.header_effective_width <= 0:
            raise ValueError("header_effective_width must be positive")
