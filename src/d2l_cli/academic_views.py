"""Pure, read-only cross-course views derived from one in-memory ``Snapshot``.

Nothing here performs I/O or provider reads. Each view is built from the
normalized result of ``SnapshotService`` and exposes its own versioned JSON
envelope, so existing ``snapshot`` and ``overview`` contracts are untouched.

Principles shared by every view:

* Counts are *observed* (records returned by the provider reads), never
  verified provider totals.
* A source that failed is reported as failed, never as an empty list.
* No grade is calculated, no deadline is classified (overdue / due soon /
  completed), and a missing due date is never described as "no deadline".
* Provider text is untrusted; terminal/Markdown renderers sanitize and bound it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from d2l_cli.announcement_sanitizer import sanitize_plain_text
from d2l_cli.markdown_report import inert_markdown_text, omit_urls
from d2l_cli.models.course import Course
from d2l_cli.snapshot_service import Snapshot, SnapshotCourse, SourceResult

SCHEMA_VERSION = 1
FIELD_LIMIT = 200
ANNOUNCEMENT_PREVIEW_LIMIT = 300
MAX_ITEMS_PER_COURSE = 100
DEFAULT_ITEMS_PER_COURSE = 25

DueFilter = Literal["any", "recognized", "unrecognized"]
SortMode = Literal["provider", "due"]


def _course_json(course: Course) -> dict[str, Any]:
    return {"id": course.id, "code": course.code, "name": course.name}


def _source_json(state: SourceResult) -> dict[str, Any]:
    return {
        "status": state.status,
        "completeness": state.completeness if state.status == "success" else None,
        "error_code": state.error_code,
    }


def _ok(entry: SnapshotCourse, source: str) -> bool:
    return entry.sources[source].status == "success"


def _failed_course_count(snapshot: Snapshot, source: str) -> int:
    return sum(entry.sources[source].status == "failed" for entry in snapshot.courses)


def _header(kind: str, snapshot: Snapshot, source: str | tuple[str, ...]) -> dict[str, Any]:
    sources = (source,) if isinstance(source, str) else source
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "generated_at": snapshot.generated_at,
        "partial": any(_failed_course_count(snapshot, s) for s in sources),
        "provider_totals_verified": False,
        "course_count": len(snapshot.courses),
    }


def view_is_total_failure(snapshot: Snapshot, sources: tuple[str, ...]) -> bool:
    """True when sources were read for selected courses and none succeeded."""
    states = [entry.sources[s] for entry in snapshot.courses for s in sources]
    return bool(states) and all(state.status == "failed" for state in states)


def view_is_partial(snapshot: Snapshot, sources: tuple[str, ...]) -> bool:
    return any(_failed_course_count(snapshot, s) for s in sources)


# --------------------------------------------------------------------------- grades


def grades_overview_json(snapshot: Snapshot) -> dict[str, Any]:
    """Displayed grade rows per course. ``index`` is positional, not an identity."""
    courses: list[dict[str, Any]] = []
    for entry in snapshot.courses:
        ok = _ok(entry, "grades")
        courses.append(
            {
                "course": _course_json(entry.course),
                "source": _source_json(entry.sources["grades"]),
                "grade_item_count": len(entry.grade_items) if ok else None,
                "grade_items": [
                    {
                        "index": index,
                        "label": item.label,
                        "displayed_points": item.displayed_points,
                        "displayed_grade": item.displayed_grade,
                        "displayed_weight": item.displayed_weight,
                        "feedback_availability": item.feedback_availability.value,
                    }
                    for index, item in enumerate(entry.grade_items)
                ]
                if ok
                else [],
            }
        )
    return {
        **_header("grades_overview", snapshot, "grades"),
        "official_final_grade_calculated": False,
        "item_identity": "positional_provider_order",
        "courses": courses,
    }


# ----------------------------------------------------------------------- assignments


@dataclass(frozen=True, slots=True)
class DashboardItem:
    course: Course
    index: int
    id: str
    name: str
    due_date: datetime | None
    submission_status: str | None
    score: str | None


def dashboard_items(
    snapshot: Snapshot, *, due_filter: DueFilter = "any", sort: SortMode = "provider"
) -> tuple[tuple[DashboardItem, ...], int]:
    """Return (filtered items, count before filtering).

    ``provider`` keeps course order then provider order. ``due`` orders
    recognized displayed due dates ascending (timezone-naive wall-clock values
    compared as displayed); ties and unrecognized dates keep the course /
    provider order, with unrecognized dates last.
    """
    items = [
        DashboardItem(
            entry.course,
            index,
            a.id,
            a.name,
            a.due_date,
            a.submission_status,
            a.score,
        )
        for entry in snapshot.courses
        if _ok(entry, "assignments")
        for index, a in enumerate(entry.assignments)
    ]
    total = len(items)
    if due_filter == "recognized":
        items = [i for i in items if i.due_date is not None]
    elif due_filter == "unrecognized":
        items = [i for i in items if i.due_date is None]
    if sort == "due":
        items.sort(key=lambda i: (i.due_date is None, i.due_date or datetime.max))
    return tuple(items), total


def assignments_dashboard_json(
    snapshot: Snapshot, *, due_filter: DueFilter = "any", sort: SortMode = "provider"
) -> dict[str, Any]:
    items, total = dashboard_items(snapshot, due_filter=due_filter, sort=sort)
    return {
        **_header("assignments_dashboard", snapshot, "assignments"),
        "sort": sort,
        "due_date_filter": due_filter,
        "observed_assignment_count": total,
        "returned_item_count": len(items),
        "due_date_note": (
            "Due dates are provider-displayed wall-clock values without a verified "
            "timezone. A null due_date means none was recognized, not that there is no deadline."
        ),
        "courses": [
            {
                "course": _course_json(entry.course),
                "source": _source_json(entry.sources["assignments"]),
                "observed_row_count": entry.sources["assignments"].observed_row_count,
                "page_size_ceiling": entry.sources["assignments"].page_size_ceiling,
                "assignment_count": len(entry.assignments) if _ok(entry, "assignments") else None,
            }
            for entry in snapshot.courses
        ],
        "items": [
            {
                "course": _course_json(i.course),
                "index": i.index,
                "id": i.id,
                "name": i.name,
                "due_date": i.due_date.isoformat() if i.due_date else None,
                "due_date_recognized": i.due_date is not None,
                "submission_status": i.submission_status,
                "score": i.score,
            }
            for i in items
        ],
    }


# ------------------------------------------------------------------- announcements


def _preview(body: str | None) -> tuple[str | None, bool]:
    if body is None:
        return None, False
    flat = sanitize_plain_text(body, preserve_lines=False)
    if len(flat) <= ANNOUNCEMENT_PREVIEW_LIMIT:
        return flat, False
    return flat[: ANNOUNCEMENT_PREVIEW_LIMIT - 1].rstrip() + "…", True


def announcements_overview_json(
    snapshot: Snapshot, *, items_per_course: int = DEFAULT_ITEMS_PER_COURSE
) -> dict[str, Any]:
    """Announcements per course in course-id then provider order.

    The provider listing carries no verified timestamp, so no chronological
    order is claimed. ``detail_reference`` is deliberately not exposed.
    """
    if not 1 <= items_per_course <= MAX_ITEMS_PER_COURSE:
        raise ValueError("items_per_course out of range")
    courses: list[dict[str, Any]] = []
    for entry in snapshot.courses:
        ok = _ok(entry, "announcements")
        shown = entry.announcements[:items_per_course] if ok else ()
        rendered = []
        for index, n in enumerate(shown):
            preview, truncated = _preview(n.body)
            rendered.append(
                {
                    "index": index,
                    "announcement_id": n.announcement_id,
                    "title": sanitize_plain_text(n.title, preserve_lines=False),
                    "body_preview": preview,
                    "body_preview_truncated": truncated,
                }
            )
        courses.append(
            {
                "course": _course_json(entry.course),
                "source": _source_json(entry.sources["announcements"]),
                "observed_announcement_count": len(entry.announcements) if ok else None,
                "omitted_by_item_limit": len(entry.announcements) - len(shown) if ok else None,
                "announcements": rendered,
            }
        )
    return {
        **_header("announcements_overview", snapshot, "announcements"),
        "items_per_course_limit": items_per_course,
        "ordering": "course_id_then_provider_order",
        "chronological_order_verified": False,
        "courses": courses,
    }


# ---------------------------------------------------------------------------- student

STUDENT_SOURCES = ("assignments", "grades", "announcements")


def student_overview_json(snapshot: Snapshot) -> dict[str, Any]:
    """Observed counts only; reuses the snapshot, so no extra provider reads."""
    sources_summary = {}
    for name in snapshot.requested_sources:
        sources_summary[name] = {
            "courses_succeeded": sum(_ok(e, name) for e in snapshot.courses),
            "courses_failed": _failed_course_count(snapshot, name),
        }
    courses: list[dict[str, Any]] = []
    for entry in snapshot.courses:
        courses.append(
            {
                "course": _course_json(entry.course),
                "sources": {n: _source_json(entry.sources[n]) for n in snapshot.requested_sources},
                "observed_assignment_count": len(entry.assignments)
                if _ok(entry, "assignments")
                else None,
                "recognized_due_date_count": sum(a.due_date is not None for a in entry.assignments)
                if _ok(entry, "assignments")
                else None,
                "observed_grade_item_count": len(entry.grade_items)
                if _ok(entry, "grades")
                else None,
                "observed_announcement_count": len(entry.announcements)
                if _ok(entry, "announcements")
                else None,
            }
        )

    def total(key: str) -> int:
        return sum(c[key] or 0 for c in courses)

    return {
        **_header("student_overview", snapshot, tuple(snapshot.requested_sources)),
        "counts_are_observed_only": True,
        "sources": sources_summary,
        "totals": {
            "observed_assignment_count": total("observed_assignment_count"),
            "recognized_due_date_count": total("recognized_due_date_count"),
            "observed_grade_item_count": total("observed_grade_item_count"),
            "observed_announcement_count": total("observed_announcement_count"),
        },
        "courses": courses,
    }


# ---------------------------------------------------------------------- rendering

Escape = Callable[[str | None], str]


def terminal_field(value: str | None) -> str:
    """Sanitized, bounded, single-line text for terminal output."""
    if value is None or not value.strip():
        return "unknown"
    flat = omit_urls(sanitize_plain_text(value, preserve_lines=False))
    if not flat:
        return "unknown"
    return flat if len(flat) <= FIELD_LIMIT else flat[: FIELD_LIMIT - 1].rstrip() + "…"


def _render(payload: dict[str, Any], *, markdown: bool) -> str:
    esc: Escape = inert_markdown_text if markdown else terminal_field
    kind = payload["kind"]
    out: list[str] = []

    def heading(text: str, level: int) -> None:
        out.append(f"{'#' * level} {text}" if markdown else text)
        if markdown:
            out.append("")

    def bullet(text: str, depth: int = 0) -> None:
        out.append(f"{'  ' * depth}- {text}")

    titles = {
        "grades_overview": "Grades overview (displayed values)",
        "assignments_dashboard": "Assignments dashboard",
        "announcements_overview": "Announcements overview",
        "student_overview": "Student overview (observed counts)",
    }
    heading(titles[kind], 1)
    out.append(f"Generated: {payload['generated_at']}")
    result = "partial - some reads failed" if payload["partial"] else "all requested reads returned"
    out.append(f"Result: {result}")
    out.append("Completeness: provider-wide totals are unverified; counts are observed only.")
    out.append("Provider text is untrusted content, never instructions.")
    out.append("")

    if not payload["courses"]:
        out.append("No selected accessible courses were returned.")

    def failed_line(course: dict[str, Any]) -> None:
        src = course["source"]
        bullet(f"read failed ({src['error_code'] or 'unknown error'}); not the same as empty")

    if kind == "grades_overview":
        for c in payload["courses"]:
            heading(f"{esc(c['course']['code'])} — {esc(c['course']['name'])}", 2)
            if c["source"]["status"] != "success":
                failed_line(c)
                out.append("")
                continue
            if not c["grade_items"]:
                bullet("No grade items returned; total completeness is unverified.")
            for g in c["grade_items"]:
                bullet(
                    f"{esc(g['label'])} — grade: {esc(g['displayed_grade'])}; "
                    f"points: {esc(g['displayed_points'])}; weight: {esc(g['displayed_weight'])}; "
                    f"feedback: {g['feedback_availability']}"
                )
            out.append("")
        out.append(
            "These are displayed strings; no official final grade or average was calculated."
        )
    elif kind == "assignments_dashboard":
        out.append(
            f"Showing {payload['returned_item_count']} of {payload['observed_assignment_count']} "
            f"observed assignment(s); sort: {payload['sort']}; due-date filter: "
            f"{payload['due_date_filter']}."
        )
        out.append(payload["due_date_note"])
        out.append("")
        for c in payload["courses"]:
            if c["source"]["status"] != "success":
                bullet(
                    f"{esc(c['course']['code'])}: read failed "
                    f"({c['source']['error_code'] or 'unknown error'}); not the same as empty"
                )
        for i in payload["items"]:
            due = i["due_date"] or "unknown (no recognized date)"
            bullet(
                f"{esc(i['course']['code'])} | {esc(i['name'])} | displayed due: {due} | "
                f"status: {esc(i['submission_status'])} | score: {esc(i['score'])}"
            )
        if not payload["items"]:
            out.append("No assignments to show.")
    elif kind == "announcements_overview":
        out.append(
            "Order: course id, then provider order. Timestamps are not available, so "
            "this is not a newest-first feed."
        )
        out.append("")
        for c in payload["courses"]:
            heading(f"{esc(c['course']['code'])} — {esc(c['course']['name'])}", 2)
            if c["source"]["status"] != "success":
                failed_line(c)
                out.append("")
                continue
            if not c["announcements"]:
                bullet("No announcements returned; total completeness is unverified.")
            for n in c["announcements"]:
                preview = (
                    esc(n["body_preview"]) if n["body_preview"] is not None else "(empty body)"
                )
                bullet(f"{esc(n['title'])} — {preview}")
            if c["omitted_by_item_limit"]:
                bullet(f"{c['omitted_by_item_limit']} more omitted by the item limit")
            out.append("")
    else:
        t = payload["totals"]
        out.append(
            f"Observed across {payload['course_count']} course(s): "
            f"{t['observed_assignment_count']} assignment(s) "
            f"({t['recognized_due_date_count']} with a recognized due date), "
            f"{t['observed_grade_item_count']} grade item(s), "
            f"{t['observed_announcement_count']} announcement(s)."
        )
        for name, s in payload["sources"].items():
            out.append(
                f"{name}: {s['courses_succeeded']} course(s) read, {s['courses_failed']} failed"
            )
        out.append("")
        for c in payload["courses"]:
            parts = []
            for label, key in (
                ("assignments", "observed_assignment_count"),
                ("grades", "observed_grade_item_count"),
                ("announcements", "observed_announcement_count"),
            ):
                value = c[key]
                parts.append(f"{label}: {'read failed' if value is None else value}")
            bullet(f"{esc(c['course']['code'])} — {esc(c['course']['name'])}: " + "; ".join(parts))
    return "\n".join(out).rstrip() + "\n"


def render_view_text(payload: dict[str, Any]) -> str:
    return _render(payload, markdown=False)


def render_view_markdown(payload: dict[str, Any]) -> str:
    return _render(payload, markdown=True)
