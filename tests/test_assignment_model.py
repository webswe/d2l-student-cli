from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from d2l_cli.models.assignment import Assignment


def _assignment(**overrides: object) -> Assignment:
    defaults: dict[str, object] = {
        "id": "90210",
        "course_id": "620000",
        "name": "Homework 1",
        "submission_status": "Submitted",
        "score": "18 / 20",
    }
    defaults.update(overrides)
    return Assignment(**defaults)  # type: ignore[arg-type]


def test_assignment_fields_round_trip() -> None:
    assignment = _assignment()
    assert assignment.id == "90210"
    assert assignment.course_id == "620000"
    assert assignment.name == "Homework 1"
    assert assignment.submission_status == "Submitted"
    assert assignment.score == "18 / 20"


def test_assignment_is_immutable() -> None:
    assignment = _assignment()
    with pytest.raises(dataclasses.FrozenInstanceError):
        assignment.name = "changed"  # type: ignore[misc]


def test_assignment_allows_missing_status_and_score() -> None:
    assignment = _assignment(submission_status=None, score=None)
    assert assignment.submission_status is None
    assert assignment.score is None


def test_assignment_due_date_is_optional_and_defaults_to_none() -> None:
    # Phase 3B replaces the Phase 2B decision to omit due_date entirely:
    # Phase 3A proved the list page carries it, so it is modeled -- as an
    # optional field, because most assignments show no due date.
    assert _assignment().due_date is None


def test_assignment_due_date_accepts_a_naive_datetime() -> None:
    due = datetime(2026, 10, 2, 23, 59)
    assert _assignment(due_date=due).due_date == due


def test_assignment_due_date_rejects_a_timezone_aware_value() -> None:
    # The provider displays no timezone, so none may be asserted.
    with pytest.raises(ValueError, match="timezone-naive"):
        _assignment(due_date=datetime(2026, 10, 2, 23, 59, tzinfo=UTC))


def test_assignment_model_still_has_no_availability_fields() -> None:
    # Availability dates exist on the page but their meaning (start/end/
    # release/visibility) was never established, so none is modeled.
    names = {f.name for f in dataclasses.fields(Assignment)}
    assert names == {"id", "course_id", "name", "submission_status", "score", "due_date"}
