"""Evidence-bounded completeness metadata for one Assignment-list response."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from d2l_cli.models.assignment import Assignment


class AssignmentCompletenessStatus(StrEnum):
    """What one rendered page proves about its page-size boundary."""

    POTENTIALLY_TRUNCATED = "potentially_truncated"
    BELOW_PAGE_SIZE_BOUNDARY = "below_page_size_boundary"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AssignmentCompleteness:
    """Assessment of one response, without claiming total-result completeness."""

    status: AssignmentCompletenessStatus
    observed_row_count: int
    page_size_ceiling: int | None


@dataclass(frozen=True, slots=True)
class AssignmentListPage:
    """Normalized assignments plus evidence from their source response."""

    assignments: tuple[Assignment, ...]
    completeness: AssignmentCompleteness
