"""Normalized assignment model.

Represents only the fields reconnaissance directly established for the
Brightspace Assignments ("Dropbox") list (see docs/PROVIDER_NOTES.md, "Phase 2A --
Assignment Reconnaissance" and "Phase 3A -- Assignment Due-Date
Reconnaissance"). Deliberately excludes fields that reconnaissance did NOT
confirm rather than guessing at them:

- ``availability`` -- availability-related date text exists in the list
  HTML (Phase 3A), but which date means start/end/release/visibility was
  never established, so none is exposed, not even as raw text.
- ``feedback`` -- the observed ``folder_user_view_feedback.d2l`` route is
  an *action link* (open a feedback page), not a feedback *value* rendered
  in the list itself; no feedback text/value column was observed.
- Any route/action metadata (submit link, feedback link, etc.) -- kept out
  of the normalized model entirely. The list provider only needs to
  *read*; exposing mutation-adjacent route metadata on the domain model
  would blur the GET-only boundary this project maintains everywhere else
  (see docs/THREAT_MODEL.md), for no confirmed reader/CLI need yet.

``due_date`` (Phase 3B) is the provider-DISPLAYED due date/time, parsed
from the one structure Phase 3A proved carries it. It is a timezone-NAIVE
``datetime`` and means exactly "the wall-clock date and time Brightspace
displayed": the page shows no timezone, and none was established, so none is
asserted -- it is not UTC, not the machine's timezone, and not converted or
localized. A consumer that needs an instant must decide the timezone itself.
``None`` means the list page rendered no due date for that assignment (or
rendered one this parser does not positively recognize); it is not an error.
A timezone-aware value is rejected at construction so the "no timezone is
claimed" invariant cannot silently erode.

``submission_status`` and ``score`` are kept as the provider's raw display
text, not parsed into an enum/number: Phase 2A confirmed these columns
exist, but not a closed set of status values or a single score format
(points vs. percent vs. text vs. ungraded/blank all remain plausible) --
same "don't invent a format we haven't observed" approach already used for
``Course.start_date``/``end_date``.

Identity is the ``(course_id, id)`` pair, not ``id`` alone: Phase 2A
evidence supports ``db`` (-> ``id``) participating in Assignment/Dropbox
identity and ``ou`` (-> ``course_id``) carrying course/org-unit context,
but does not establish that ``db`` is globally unique *across* courses.
Every list call is already scoped to one course (see
``d2l_cli.providers.brightspace_assignments``), so ``id`` alone is unique
within one call's results; ``course_id`` is carried on the model so a
caller aggregating results across courses still has a collision-safe key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Assignment:
    id: str
    course_id: str
    name: str
    submission_status: str | None
    score: str | None
    due_date: datetime | None = None

    def __post_init__(self) -> None:
        if self.due_date is not None and self.due_date.tzinfo is not None:
            raise ValueError(
                "Assignment.due_date must be timezone-naive: the provider displays "
                "no timezone, so none may be asserted"
            )
