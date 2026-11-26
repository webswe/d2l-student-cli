"""d2l-cli entry point.

Phase 1: ``auth login``, ``auth status``, and ``courses list`` (with
``--json``). Phase 2B/3B: ``assignments list --course <id>`` (with
``--json`` and optional due dates). ``assignments overview`` composes those
existing read-only course and assignment paths across courses. Phase 5C adds
``grades list --course <id>`` over the verified display-only grade model.
``announcements list --course <id>`` exposes the course-scoped, sanitized
News listing service. The root ``overview`` command composes existing course,
Assignment, Grades, and Announcements services without adding an endpoint. See
docs/THREAT_MODEL.md and docs/PROVIDER_NOTES.md for the security model and the
reconnaissance each provider builds on.

Layering (see the Phase 1 design report for the full picture):

    BrowserAuth -> SessionBridge -> D2LHttpClient (GET-only)
        -> BrightspaceCourseProvider -> CourseService -> CLI / JSON
        -> BrightspaceAssignmentProvider -> AssignmentService -> CLI / JSON
        -> BrightspaceGradeProvider -> GradeService -> CLI / JSON
        -> BrightspaceAnnouncementProvider -> AnnouncementService -> CLI / JSON

This module wires those layers together and formats output. It never
constructs raw provider requests or knows the raw ``mycourses`` JSON shape
or the Assignment-list HTML structure itself -- those stay inside
``d2l_cli.providers.brightspace_courses``/``brightspace_assignments``.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, NoReturn

import typer

from d2l_cli.academic_overview_service import (
    AcademicCourseOverview,
    AcademicOverviewResult,
    AcademicOverviewService,
)
from d2l_cli.agent_skill import SKILL_TEXT, generate_skill
from d2l_cli.announcement_sanitizer import sanitize_plain_text
from d2l_cli.announcement_service import AnnouncementService
from d2l_cli.assignment_overview_service import (
    AssignmentOverviewItem,
    AssignmentOverviewResult,
    AssignmentOverviewService,
    CourseAssignmentCompleteness,
)
from d2l_cli.assignment_service import AssignmentService
from d2l_cli.auth_service import AuthService, AuthState
from d2l_cli.browser_auth import BrowserAuth
from d2l_cli.calendar_service import (
    CalendarService,
    format_instant,
    make_window,
    upcoming_window,
)
from d2l_cli.client.errors import D2LError, SSOLoginFailedError
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.config import Config, load_config
from d2l_cli.content_service import ContentService
from d2l_cli.course_resolver import AmbiguousCourseError, CourseResolutionError, CourseResolver
from d2l_cli.course_service import CourseService
from d2l_cli.credentials import KeyringCredentialStore
from d2l_cli.diagnostics import diagnostics_report
from d2l_cli.discussion_service import DiscussionService
from d2l_cli.grade_service import GradeService
from d2l_cli.markdown_report import render_snapshot_markdown
from d2l_cli.models.announcement import Announcement
from d2l_cli.models.assignment import Assignment
from d2l_cli.models.assignment_completeness import (
    AssignmentCompleteness,
    AssignmentCompletenessStatus,
)
from d2l_cli.models.calendar import CalendarEvent, CalendarListing
from d2l_cli.models.content import ContentNode
from d2l_cli.models.course import Course
from d2l_cli.models.discussion import (
    DiscussionForum,
    DiscussionPost,
    DiscussionPosts,
    DiscussionTopic,
)
from d2l_cli.models.grade import GradeItem
from d2l_cli.models.quiz import AttemptListing, Quiz, QuizAttempt, QuizListing
from d2l_cli.models.syllabus import SyllabusCandidate, SyllabusDocument, SyllabusSearch
from d2l_cli.providers.brightspace_announcements import BrightspaceAnnouncementProvider
from d2l_cli.providers.brightspace_assignments import BrightspaceAssignmentProvider
from d2l_cli.providers.brightspace_calendar import BrightspaceCalendarProvider
from d2l_cli.providers.brightspace_content import BrightspaceContentProvider
from d2l_cli.providers.brightspace_courses import BrightspaceCourseProvider
from d2l_cli.providers.brightspace_discussions import BrightspaceDiscussionProvider
from d2l_cli.providers.brightspace_grades import BrightspaceGradeProvider
from d2l_cli.providers.brightspace_quizzes import BrightspaceQuizProvider
from d2l_cli.quiz_service import QuizService
from d2l_cli.session_store import SessionStore
from d2l_cli.snapshot_service import SOURCES, Snapshot, SnapshotService
from d2l_cli.syllabus_service import SyllabusService

app = typer.Typer(
    name="d2l",
    help="Personal, GET-only CLI for your own Brightspace/D2L account.",
    no_args_is_help=True,
)

def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import PackageNotFoundError, version

        try:
            installed = version("d2l-cli")
        except PackageNotFoundError:
            installed = "unknown"
        typer.echo(f"d2l-cli {installed}")
        raise typer.Exit(code=0)


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed d2l-cli version and exit.",
    ),
) -> None:
    """Personal, GET-only CLI for your own Brightspace/D2L account."""


auth_app = typer.Typer(help="Authentication state.")
app.add_typer(auth_app, name="auth")

courses_app = typer.Typer(help="Course listings.")
app.add_typer(courses_app, name="courses")

assignments_app = typer.Typer(help="Assignment listings.")
app.add_typer(assignments_app, name="assignments")

grades_app = typer.Typer(help="Visible grade-item listings.")
app.add_typer(grades_app, name="grades")

announcements_app = typer.Typer(help="Course announcement listings.")
app.add_typer(announcements_app, name="announcements")

syllabus_app = typer.Typer(help="Find and fetch a course syllabus document (read-only).")
app.add_typer(syllabus_app, name="syllabus")
calendar_app = typer.Typer(help="Read your calendar events (read-only).")
app.add_typer(calendar_app, name="calendar")
quizzes_app = typer.Typer(help="Read quiz metadata and your own attempts (read-only).")
app.add_typer(quizzes_app, name="quizzes")
discussions_app = typer.Typer(help="Read discussion forums, topics and posts (read-only).")
app.add_typer(discussions_app, name="discussions")
content_app = typer.Typer(help="Read the course content hierarchy (no downloads).")
app.add_typer(content_app, name="content")

agent_app = typer.Typer(help="Explicit local agent integration files.")
agent_skill_app = typer.Typer(help="Portable agent instruction file.")
agent_app.add_typer(agent_skill_app, name="skill")
app.add_typer(agent_app, name="agent")

_ANNOUNCEMENT_PREVIEW_CHARS = 120


@app.command("diagnostics")
def diagnostics(
    json_output: bool = typer.Option(False, "--json", help="Emit local diagnostics as JSON."),
) -> None:
    """Inspect local capabilities without credentials or provider requests."""
    report = diagnostics_report()
    if json_output:
        typer.echo(json.dumps(report))
        return
    typer.echo(f"D2L-CLI {report['application_version']} on Python {report['python_version']}")
    typer.echo(f"Configuration valid: {str(report['configuration_valid']).lower()}")
    typer.echo("Session presence: not checked; provider verification: not performed")
    missing = [name for name, available in report["dependencies"].items() if not available]
    if missing:
        typer.echo(f"Missing local dependencies: {', '.join(missing)}")


@agent_skill_app.command("generate")
def agent_skill_generate(
    output: Path | None = typer.Option(  # noqa: B008 - Typer options use argument defaults
        None, "--output", help="New file directly in the current directory."
    ),
    preview: bool = typer.Option(False, "--preview", help="Print the template without writing."),
) -> None:
    """Generate portable agent guidance only when explicitly requested."""
    if preview:
        typer.echo(SKILL_TEXT, nl=False)
        return
    if output is None:
        typer.echo("error: --output is required unless --preview is used", err=True)
        raise typer.Exit(code=2)
    try:
        target = generate_skill(output, cwd=Path.cwd())
    except (ValueError, OSError):
        typer.echo("error: unsafe, existing, or unavailable output destination", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Created {target.name}")


def _build_auth_service(config: Config) -> AuthService:
    session_store = SessionStore(KeyringCredentialStore(), origin=config.base_url)
    browser_auth = BrowserAuth()

    def verify_session(headers: Mapping[str, str]) -> None:
        client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
        try:
            BrightspaceCourseProvider(client).list_courses()
        finally:
            client.close()

    return AuthService(
        session_store=session_store,
        browser_auth=browser_auth,
        verify_session=verify_session,
    )


def _build_course_service(config: Config, headers: Mapping[str, str]) -> CourseService:
    client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
    return CourseService(BrightspaceCourseProvider(client))


def _build_assignment_service(config: Config, headers: Mapping[str, str]) -> AssignmentService:
    client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
    return AssignmentService(BrightspaceAssignmentProvider(client))


def _build_grade_service(config: Config, headers: Mapping[str, str]) -> GradeService:
    client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
    return GradeService(BrightspaceGradeProvider(client))


def _build_announcement_service(config: Config, headers: Mapping[str, str]) -> AnnouncementService:
    client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
    return AnnouncementService(BrightspaceAnnouncementProvider(client))


def _build_syllabus_service(config: Config, headers: Mapping[str, str]) -> SyllabusService:
    client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
    return SyllabusService(BrightspaceContentProvider(client))


def _build_content_service(config: Config, headers: Mapping[str, str]) -> ContentService:
    client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
    return ContentService(BrightspaceContentProvider(client))


def _build_calendar_service(config: Config, headers: Mapping[str, str]) -> CalendarService:
    client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
    return CalendarService(BrightspaceCalendarProvider(client))


def _build_quiz_service(config: Config, headers: Mapping[str, str]) -> QuizService:
    client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
    return QuizService(BrightspaceQuizProvider(client))


def _build_discussion_service(config: Config, headers: Mapping[str, str]) -> DiscussionService:
    client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
    return DiscussionService(BrightspaceDiscussionProvider(client))


def _build_assignment_overview_service(
    config: Config, headers: Mapping[str, str]
) -> AssignmentOverviewService:
    # Both services share one GET-only client/session. The overview adds no
    # endpoint: one existing course-list GET selects courses, then the existing
    # assignment-list GET is used once per selected course.
    client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
    return AssignmentOverviewService(
        CourseService(BrightspaceCourseProvider(client)),
        AssignmentService(BrightspaceAssignmentProvider(client)),
    )


def _build_academic_overview_service(
    config: Config, headers: Mapping[str, str]
) -> AcademicOverviewService:
    # One shared authenticated GET-only client serves one course-list read and
    # the established Assignment/Grades/Announcements reads once per selected
    # course.
    client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
    return AcademicOverviewService(
        CourseService(BrightspaceCourseProvider(client)),
        AssignmentService(BrightspaceAssignmentProvider(client)),
        GradeService(BrightspaceGradeProvider(client)),
        AnnouncementService(BrightspaceAnnouncementProvider(client)),
    )


def _build_snapshot_service(config: Config, headers: Mapping[str, str]) -> SnapshotService:
    client = D2LHttpClient(config.base_url, auth_header_provider=lambda: headers)
    return SnapshotService(
        CourseService(BrightspaceCourseProvider(client)),
        AssignmentService(BrightspaceAssignmentProvider(client)),
        GradeService(BrightspaceGradeProvider(client)),
        AnnouncementService(BrightspaceAnnouncementProvider(client)),
    )


def _snapshot_to_json(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": snapshot.generated_at,
        "requested_sources": list(snapshot.requested_sources),
        "successful_sources": list(snapshot.successful_sources),
        "partial": snapshot.partial,
        "service_calls": snapshot.service_calls,
        "http_request_count": None,
        "courses": [
            {
                "course": _course_to_json(entry.course),
                "sources": {
                    name: {
                        "status": state.status,
                        "completeness": state.completeness,
                        "error_code": state.error_code,
                        "observed_row_count": state.observed_row_count,
                        "page_size_ceiling": state.page_size_ceiling,
                    }
                    for name, state in entry.sources.items()
                },
                "assignments": [_assignment_to_json(a) for a in entry.assignments],
                "grade_items": [_grade_item_to_json(g) for g in entry.grade_items],
                "announcements": [_announcement_to_json(n) for n in entry.announcements],
            }
            for entry in snapshot.courses
        ],
    }


def _course_to_json(course: Course) -> dict[str, Any]:
    return {
        "id": course.id,
        "code": course.code,
        "name": course.name,
        "semester_name": course.semester_name,
        "is_active": course.is_active,
        "can_access": course.can_access,
        "is_new_enrollment": course.is_new_enrollment,
        "start_date": course.start_date,
        "end_date": course.end_date,
    }


def _assignment_to_json(assignment: Assignment) -> dict[str, Any]:
    # Only the normalized, supported fields -- no raw HTML, no provider
    # routes, no availability/feedback (see d2l_cli.models.assignment's
    # docstring for why those are omitted entirely rather than included as
    # null). due_date is an ISO-8601 string WITHOUT a UTC offset (the
    # provider displays no timezone; none is asserted), or null.
    return {
        "id": assignment.id,
        "course_id": assignment.course_id,
        "name": assignment.name,
        "submission_status": assignment.submission_status,
        "score": assignment.score,
        "due_date": assignment.due_date.isoformat() if assignment.due_date else None,
    }


def _grade_item_to_json(item: GradeItem) -> dict[str, Any]:
    # These are display strings, not numeric or academic interpretations.
    # Feedback content and parser metadata are intentionally absent.
    return {
        "course_id": item.course_id,
        "label": item.label,
        "displayed_points": item.displayed_points,
        "displayed_grade": item.displayed_grade,
        "displayed_weight": item.displayed_weight,
        "feedback_availability": item.feedback_availability.value,
    }


def _announcement_to_json(announcement: Announcement) -> dict[str, Any]:
    return {
        "course_id": announcement.course_id,
        "announcement_id": announcement.announcement_id,
        "title": announcement.title,
        "body": announcement.body,
        "detail_reference": announcement.detail_reference,
    }


def _terminal_text(value: str) -> str:
    """Render provider-controlled text without terminal control sequences.

    JSON output intentionally retains the established machine contract; this
    boundary is only for human-facing terminal output.
    """
    return sanitize_plain_text(value, preserve_lines=False)


def _announcement_title_for_terminal(announcement: Announcement) -> str:
    return _terminal_text(announcement.title)


def _announcement_body_for_terminal(announcement: Announcement) -> str | None:
    if announcement.body is None:
        return None
    return sanitize_plain_text(announcement.body) or None


def _announcement_preview(announcement: Announcement) -> str:
    body = _announcement_body_for_terminal(announcement)
    if body is None:
        return "(empty body)"
    one_line = sanitize_plain_text(body, preserve_lines=False)
    if len(one_line) <= _ANNOUNCEMENT_PREVIEW_CHARS:
        return one_line
    return one_line[: _ANNOUNCEMENT_PREVIEW_CHARS - 3].rstrip() + "..."


def _overview_item_to_json(item: AssignmentOverviewItem) -> dict[str, Any]:
    assignment = item.assignment
    return {
        "id": assignment.id,
        "course_id": assignment.course_id,
        "course_code": item.course.code,
        "course_name": item.course.name,
        "name": assignment.name,
        "due_date": assignment.due_date.isoformat() if assignment.due_date else None,
    }


def _completeness_to_json(completeness: AssignmentCompleteness) -> dict[str, Any]:
    return {
        "status": completeness.status.value,
        "observed_row_count": completeness.observed_row_count,
        "page_size_ceiling": completeness.page_size_ceiling,
    }


def _course_completeness_to_json(
    result: CourseAssignmentCompleteness,
) -> dict[str, Any]:
    return {
        "course_id": result.course.id,
        "course_code": result.course.code,
        **_completeness_to_json(result.completeness),
    }


def _academic_course_to_json(entry: AcademicCourseOverview) -> dict[str, Any]:
    return {
        "course": _course_to_json(entry.course),
        "retrieved_assignment_count": entry.retrieved_assignment_count,
        "recognized_due_date_count": entry.recognized_due_date_count,
        "without_recognized_due_date_count": entry.without_recognized_due_date_count,
        "visible_grade_item_count": entry.visible_grade_item_count,
        "assignment_completeness": _completeness_to_json(entry.assignment_completeness),
        "grade_completeness": entry.grade_completeness.value,
        "assignments": [_assignment_to_json(assignment) for assignment in entry.assignments],
        "grade_items": [_grade_item_to_json(item) for item in entry.grade_items],
        "retrieved_announcement_count": entry.retrieved_announcement_count,
        "announcement_completeness": entry.announcement_completeness.value,
        "announcements": [
            _announcement_to_json(announcement) for announcement in entry.announcements
        ],
    }


def _warn_if_uncertain(completeness: AssignmentCompleteness) -> None:
    if completeness.status == AssignmentCompletenessStatus.BELOW_PAGE_SIZE_BOUNDARY:
        return
    if completeness.status == AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED:
        detail = (
            f"observed {completeness.observed_row_count} assignment-shaped rows, "
            f"reaching the selected page-size ceiling of {completeness.page_size_ceiling}"
        )
        typer.echo(
            f"warning: assignment results for this course may be truncated ({detail}); "
            "GET-only pagination is unavailable.",
            err=True,
        )
        return
    typer.echo(
        "warning: assignment result completeness for this course is unknown because the "
        "page-size boundary could not be assessed reliably.",
        err=True,
    )


def _warn_assignment_overview_completeness(result: AssignmentOverviewResult) -> None:
    potentially_truncated = sum(
        course_result.completeness.status == AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED
        for course_result in result.course_completeness
    )
    unknown = sum(
        course_result.completeness.status == AssignmentCompletenessStatus.UNKNOWN
        for course_result in result.course_completeness
    )
    if potentially_truncated or unknown:
        typer.echo(
            "warning: assignment-list completeness is uncertain for "
            f"{potentially_truncated + unknown} course(s) "
            f"({potentially_truncated} reached the observed page-size boundary; "
            f"{unknown} could not be assessed).",
            err=True,
        )


def _warn_academic_overview_completeness(result: AcademicOverviewResult) -> None:
    potentially_truncated = sum(
        entry.assignment_completeness.status == AssignmentCompletenessStatus.POTENTIALLY_TRUNCATED
        for entry in result.courses
    )
    assignment_unknown = sum(
        entry.assignment_completeness.status == AssignmentCompletenessStatus.UNKNOWN
        for entry in result.courses
    )
    if potentially_truncated or assignment_unknown:
        typer.echo(
            "warning: assignment-list completeness is uncertain for "
            f"{potentially_truncated + assignment_unknown} course(s) "
            f"({potentially_truncated} reached the observed page-size boundary; "
            f"{assignment_unknown} could not be assessed).",
            err=True,
        )
    if result.courses:
        typer.echo(
            "warning: grade-list completeness cannot be verified for "
            f"{len(result.courses)} course(s); status is unknown.",
            err=True,
        )
        typer.echo(
            "warning: announcement-list completeness cannot be verified for "
            f"{len(result.courses)} course(s); status is unknown.",
            err=True,
        )


def _fail(*, json_output: bool, message: str) -> NoReturn:
    # Errors always go to stderr, whether or not --json was requested, so
    # --json's stdout stays pure JSON (or empty) for a downstream consumer.
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=1)


def _load_cli_config() -> Config:
    try:
        return load_config()
    except D2LError as exc:
        _fail(json_output=False, message=str(exc))


@auth_app.command("login")
def auth_login() -> None:
    """Launch an isolated browser to complete SSO/MFA and store the resulting session securely,
    bound to the configured site."""
    config = _load_cli_config()
    service = _build_auth_service(config)
    try:
        service.login(config.base_url)
    except SSOLoginFailedError as exc:
        _fail(json_output=False, message=f"login did not complete: {exc}")
    except D2LError as exc:
        _fail(json_output=False, message=str(exc))
    typer.echo("Login successful; session stored securely.")


@auth_app.command("status")
def auth_status() -> None:
    """Report whether an authenticated session is available."""
    config = _load_cli_config()
    service = _build_auth_service(config)
    result = service.status()
    typer.echo(f"{result.state.value}: {result.detail}")
    if result.state != AuthState.AUTHENTICATED:
        raise typer.Exit(code=1)


@courses_app.command("list")
def courses_list(
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
) -> None:
    """List your enrolled courses."""
    config = _load_cli_config()
    auth = _build_auth_service(config)

    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    try:
        courses = _build_course_service(config, headers).list_courses()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    if json_output:
        typer.echo(json.dumps({"courses": [_course_to_json(c) for c in courses]}))
        return

    if not courses:
        typer.echo("No courses found.")
        return
    for course in courses:
        typer.echo(
            f"{_terminal_text(course.code)}\t{_terminal_text(course.name)}\t"
            f"{_terminal_text(course.semester_name)}"
        )


@assignments_app.command("list")
def assignments_list(
    course_id: str = typer.Option(
        ..., "--course", help="Brightspace course (org-unit) id -- see 'd2l courses list'."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
    include_completeness: bool = typer.Option(
        False,
        "--include-completeness",
        help="With --json, add source-page completeness metadata.",
    ),
) -> None:
    """List assignments for a course.

    The due date, when the list page shows one, is the provider-displayed
    date/time with NO timezone asserted. Unsupported fields (availability,
    feedback, action routes) are intentionally absent -- see docs/PROVIDER_NOTES.md,
    "Phase 2A" / "Phase 3A" / "Phase 3B". Requires an explicit course id;
    this command does not search or fuzzy-match by course name/title.

    Text output is tab-separated: name, status, score, due (``-`` when
    absent); the due column is last so earlier columns keep their positions.
    """
    config = _load_cli_config()
    auth = _build_auth_service(config)

    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    try:
        page = _build_assignment_service(config, headers).get_assignment_list(course_id)
    except ValueError as exc:
        _fail(json_output=json_output, message=str(exc))
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    _warn_if_uncertain(page.completeness)
    assignments = page.assignments

    if json_output:
        payload: dict[str, Any] = {"assignments": [_assignment_to_json(a) for a in assignments]}
        if include_completeness:
            payload["completeness"] = _completeness_to_json(page.completeness)
        typer.echo(json.dumps(payload))
        return

    if not assignments:
        typer.echo("No assignments found.")
        return
    for assignment in assignments:
        status = _terminal_text(assignment.submission_status or "-")
        score = _terminal_text(assignment.score or "-")
        due = assignment.due_date.isoformat() if assignment.due_date else "-"
        typer.echo(f"{_terminal_text(assignment.name)}\t{status}\t{score}\t{due}")


@assignments_app.command("overview")
def assignments_overview(
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
    chronological: bool = typer.Option(
        False,
        "--chronological",
        help="Sort recognized due dates earliest first; assignments without due dates come last.",
    ),
    all_courses: bool = typer.Option(
        False,
        "--all-courses",
        help="Include inactive accessible courses; the default is active accessible courses only.",
    ),
    include_completeness: bool = typer.Option(
        False,
        "--include-completeness",
        help="With --json, add per-course source-page completeness metadata.",
    ),
) -> None:
    """List assignments across courses using existing GET-only providers.

    Text output is tab-separated: course code, course name, assignment name,
    due date (``-`` when absent). Due dates remain timezone-naive provider
    display values. No overdue/due-soon/completion state is inferred.
    """
    config = _load_cli_config()
    auth = _build_auth_service(config)

    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    try:
        result: AssignmentOverviewResult = _build_assignment_overview_service(
            config, headers
        ).get_overview(
            active_only=not all_courses,
            chronological=chronological,
        )
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    _warn_assignment_overview_completeness(result)
    items = result.items

    if json_output:
        payload = {"assignments": [_overview_item_to_json(item) for item in items]}
        if include_completeness:
            payload["course_completeness"] = [
                _course_completeness_to_json(course_result)
                for course_result in result.course_completeness
            ]
        typer.echo(json.dumps(payload))
        return

    if not items:
        scope = "accessible courses" if all_courses else "active accessible courses"
        typer.echo(f"No assignments found across {scope}.")
        return
    for item in items:
        due = item.assignment.due_date.isoformat() if item.assignment.due_date else "-"
        typer.echo(
            f"{_terminal_text(item.course.code)}\t"
            f"{_terminal_text(item.course.name)}\t"
            f"{_terminal_text(item.assignment.name)}\t{due}"
        )


@app.command("snapshot")
def academic_snapshot(
    json_output: bool = typer.Option(False, "--json", help="Emit the versioned JSON snapshot."),
    output_format: str = typer.Option("text", "--format", help="Human report: text or markdown."),
    source: str = typer.Option(
        ",".join(SOURCES),
        "--sources",
        help="Comma-separated sources: assignments,grades,announcements.",
    ),
    max_courses: int = typer.Option(10, "--max-courses", help="Maximum selected courses (1-20)."),
    all_courses: bool = typer.Option(False, "--all-courses"),
) -> None:
    """Read a bounded academic snapshot from existing course-scoped services."""
    if output_format not in {"text", "markdown"} or (json_output and output_format != "text"):
        typer.echo(
            "error: --format accepts text or markdown and cannot be combined with --json",
            err=True,
        )
        raise typer.Exit(code=2)
    selected = tuple(part.strip() for part in source.split(","))
    if (
        not selected
        or len(set(selected)) != len(selected)
        or any(s not in SOURCES for s in selected)
    ):
        typer.echo("error: --sources requires unique supported sources", err=True)
        raise typer.Exit(code=2)
    if not 1 <= max_courses <= 20:
        typer.echo("error: --max-courses must be between 1 and 20", err=True)
        raise typer.Exit(code=2)
    config = _load_cli_config()
    try:
        headers = _build_auth_service(config).header_provider()
        result = _build_snapshot_service(config, headers).get_snapshot(
            sources=selected, active_only=not all_courses, max_courses=max_courses
        )
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))
    except ValueError:
        _fail(json_output=json_output, message="snapshot scope or normalized data is invalid")

    successes = sum(
        entry.sources[name].status == "success"
        for entry in result.courses
        for name in result.requested_sources
    )
    if json_output:
        typer.echo(json.dumps(_snapshot_to_json(result)))
    elif output_format == "markdown":
        typer.echo(render_snapshot_markdown(result), nl=False)
    else:
        typer.echo(
            f"Snapshot: {len(result.courses)} course(s), "
            f"{successes} successful source read(s), partial={str(result.partial).lower()}"
        )
        for entry in result.courses:
            typer.echo(f"  {_terminal_text(entry.course.code)}:")
            for name, count in (
                ("assignments", len(entry.assignments)),
                ("grades", len(entry.grade_items)),
                ("announcements", len(entry.announcements)),
            ):
                state = entry.sources[name]
                detail = f"{count} returned" if state.status == "success" else state.status
                typer.echo(f"    {name}: {detail}")
    if result.partial:
        typer.echo("warning: snapshot contains failed sources; inspect source statuses", err=True)
    if result.partial and successes == 0:
        raise typer.Exit(code=1)


@app.command("overview")
def academic_overview(
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
    all_courses: bool = typer.Option(
        False,
        "--all-courses",
        help="Include inactive accessible courses; the default is active accessible courses only.",
    ),
    details: bool = typer.Option(
        False,
        "--details",
        help="In text mode, show all assignments, grades, and announcement bodies.",
    ),
) -> None:
    """Summarize courses, assignments, visible grades, and announcements.

    Counts describe records returned by the existing providers, not complete
    Brightspace totals. Due dates retain their timezone-naive display meaning;
    no deadline status, grade performance, or announcement recency is inferred.
    """
    config = _load_cli_config()
    auth = _build_auth_service(config)

    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    try:
        result = _build_academic_overview_service(config, headers).get_overview(
            active_only=not all_courses
        )
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    _warn_academic_overview_completeness(result)

    if json_output:
        typer.echo(
            json.dumps({"courses": [_academic_course_to_json(entry) for entry in result.courses]})
        )
        return

    if not result.courses:
        scope = "accessible courses" if all_courses else "active accessible courses"
        typer.echo(f"No {scope} found.")
        return

    preview_limit = 3
    details_available = False
    for position, entry in enumerate(result.courses):
        if position:
            typer.echo("")
        typer.echo(
            f"Course: {_terminal_text(entry.course.code)} - {_terminal_text(entry.course.name)}"
        )
        typer.echo(f"  Retrieved assignments: {entry.retrieved_assignment_count}")
        typer.echo(f"  Recognized due dates: {entry.recognized_due_date_count}")
        typer.echo(f"  Without recognized due dates: {entry.without_recognized_due_date_count}")
        typer.echo(f"  Visible grade items: {entry.visible_grade_item_count}")
        typer.echo(f"  Retrieved announcements: {entry.retrieved_announcement_count}")

        shown_assignments = entry.assignments if details else entry.assignments[:preview_limit]
        if shown_assignments:
            typer.echo(
                "  Assignments "
                f"(showing {len(shown_assignments)} of {entry.retrieved_assignment_count}):"
            )
            for assignment in shown_assignments:
                due = assignment.due_date.isoformat() if assignment.due_date else "-"
                typer.echo(f"    {_terminal_text(assignment.name)}\t{due}")
        else:
            typer.echo("  Assignments: none retrieved.")

        shown_announcements = (
            entry.announcements if details else entry.announcements[:preview_limit]
        )
        if shown_announcements:
            typer.echo(
                "  Announcements "
                f"(showing {len(shown_announcements)} of "
                f"{entry.retrieved_announcement_count}):"
            )
            for announcement_position, announcement in enumerate(shown_announcements, start=1):
                typer.echo(
                    f"    {announcement_position}. {_announcement_title_for_terminal(announcement)}"
                )
                if details:
                    body = _announcement_body_for_terminal(announcement)
                    if body is None:
                        typer.echo("       (empty body)")
                    else:
                        for line in body.split("\n"):
                            typer.echo(f"       {line}" if line else "")
        else:
            typer.echo("  Announcements: none retrieved.")

        if details:
            if entry.grade_items:
                typer.echo("  Grade items (displayed values):")
                typer.echo("    Grade item\tPoints\tGrade\tWeight\tFeedback")
                for item in entry.grade_items:
                    points = _terminal_text(
                        item.displayed_points if item.displayed_points is not None else "-"
                    )
                    grade = _terminal_text(
                        item.displayed_grade if item.displayed_grade is not None else "-"
                    )
                    weight = _terminal_text(
                        item.displayed_weight if item.displayed_weight is not None else "-"
                    )
                    typer.echo(
                        f"    {_terminal_text(item.label)}\t{points}\t{grade}\t{weight}\t"
                        f"{item.feedback_availability.value}"
                    )
            else:
                typer.echo("  Grade items: none visible.")
        elif (
            entry.retrieved_assignment_count > len(shown_assignments)
            or entry.visible_grade_item_count > 0
            or entry.retrieved_announcement_count > 0
        ):
            details_available = True

    if details_available:
        typer.echo("")
        typer.echo(
            "Use --details to show all retrieved assignments, visible grade-item "
            "details, and announcement bodies."
        )


@grades_app.command("list")
def grades_list(
    course_id: str = typer.Option(
        ..., "--course", help="Brightspace course (org-unit) id -- see 'd2l courses list'."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
    include_completeness: bool = typer.Option(
        False,
        "--include-completeness",
        help="With --json, add the evidence-bounded list completeness status.",
    ),
) -> None:
    """List visible grade items for one explicitly selected course.

    Points, grade, and weight are the strings Brightspace displays. They are
    never parsed, calculated, or assigned academic states. A ``-`` in text
    output means the corresponding displayed value is absent; JSON uses null.
    Feedback reports availability only and never includes feedback content.
    """
    config = _load_cli_config()
    auth = _build_auth_service(config)

    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    try:
        page = _build_grade_service(config, headers).get_grade_list(course_id)
    except ValueError as exc:
        _fail(json_output=json_output, message=str(exc))
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    items = page.items
    if any(item.course_id != course_id for item in items):
        _fail(
            json_output=json_output,
            message="grade results did not match the requested course",
        )

    if json_output:
        payload: dict[str, Any] = {"grade_items": [_grade_item_to_json(item) for item in items]}
        if include_completeness:
            payload["completeness"] = page.completeness.value
        typer.echo(json.dumps(payload))
        return

    typer.echo(
        "warning: grade-list completeness cannot be verified; status is unknown.",
        err=True,
    )

    if not items:
        typer.echo("No visible grade items found.")
        return

    typer.echo("Grade item\tPoints\tGrade\tWeight\tFeedback")
    for item in items:
        points = _terminal_text(item.displayed_points if item.displayed_points is not None else "-")
        grade = _terminal_text(item.displayed_grade if item.displayed_grade is not None else "-")
        weight = _terminal_text(item.displayed_weight if item.displayed_weight is not None else "-")
        typer.echo(
            f"{_terminal_text(item.label)}\t{points}\t{grade}\t{weight}\t"
            f"{item.feedback_availability.value}"
        )


@announcements_app.command("list")
def announcements_list(
    course_id: str = typer.Option(
        ..., "--course", help="Brightspace course (org-unit) id -- see 'd2l courses list'."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
    details: bool = typer.Option(
        False,
        "--details",
        help="In text mode, show each complete sanitized body.",
    ),
    include_completeness: bool = typer.Option(
        False,
        "--include-completeness",
        help="With --json, add the evidence-bounded list completeness status.",
    ),
) -> None:
    """List sanitized announcements for one explicitly selected course.

    The default text view shows each title and a short one-line body preview.
    ``--details`` shows the complete sanitized plain-text body. JSON has the
    stable shape ``{"announcements": [...]}``; each record contains only
    ``course_id``, ``announcement_id``, ``title``, ``body``, and
    ``detail_reference``. No detail pages or embedded resources are loaded.
    """
    config = _load_cli_config()
    auth = _build_auth_service(config)

    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    try:
        page = _build_announcement_service(config, headers).get_announcement_list(course_id)
    except ValueError as exc:
        _fail(json_output=json_output, message=str(exc))
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    announcements = page.items
    if any(announcement.course_id != course_id for announcement in announcements):
        _fail(
            json_output=json_output,
            message="announcement results did not match the requested course",
        )

    if json_output:
        payload: dict[str, Any] = {
            "announcements": [_announcement_to_json(announcement) for announcement in announcements]
        }
        if include_completeness:
            payload["completeness"] = page.completeness.value
        typer.echo(json.dumps(payload))
        return

    typer.echo(
        "warning: announcement-list completeness cannot be verified; status is unknown.",
        err=True,
    )

    if not announcements:
        typer.echo("No announcements found.")
        return

    for position, announcement in enumerate(announcements, start=1):
        if details and position > 1:
            typer.echo("")
        typer.echo(f"{position}. {_announcement_title_for_terminal(announcement)}")
        if details:
            body = _announcement_body_for_terminal(announcement)
            if body is None:
                typer.echo("   (empty body)")
                continue
            for line in body.split("\n"):
                typer.echo(f"   {line}" if line else "")
            continue
        typer.echo(f"   {_announcement_preview(announcement)}")


def _syllabus_candidate_to_json(candidate: SyllabusCandidate) -> dict[str, Any]:
    topic = candidate.topic
    return {
        "topic_id": topic.topic_id,
        "title": topic.title,
        "module_path": list(topic.module_path),
        "kind": topic.kind.value,
        "file_extension": topic.file_extension,
        "last_modified": topic.last_modified,
        "score": candidate.score,
        "reasons": list(candidate.reasons),
        "fetchable": candidate.fetchable,
    }


def _syllabus_search_to_json(search: SyllabusSearch) -> dict[str, Any]:
    return {
        "syllabus_search": {
            "course_id": search.course_id,
            "api_version": search.api_version,
            "topic_count": search.topic_count,
            "recommended_topic_id": search.recommended_topic_id,
            "candidates": [_syllabus_candidate_to_json(c) for c in search.candidates],
        }
    }


def _syllabus_document_to_json(document: SyllabusDocument) -> dict[str, Any]:
    return {
        "syllabus_document": {
            "course_id": document.course_id,
            "topic_id": document.topic_id,
            "title": document.title,
            "module_path": list(document.module_path),
            "file_name": document.file_name,
            "media_type": document.media_type,
            "size_bytes": len(document.content),
            "sha256": document.sha256,
            "api_version": document.api_version,
            "content_base64": base64.b64encode(document.content).decode("ascii"),
        }
    }


@syllabus_app.command("find")
def syllabus_find(
    course_id: str = typer.Option(
        ..., "--course", help="Brightspace course (org-unit) id -- see 'd2l courses list'."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
) -> None:
    """Rank the course's visible content topics that may be its syllabus.

    Reads the course table of contents once. JSON has the stable shape
    ``{"syllabus_search": {...}}``; ``recommended_topic_id`` is set only when exactly one
    downloadable candidate clearly leads, otherwise it is null and the caller chooses.
    """
    config = _load_cli_config()
    auth = _build_auth_service(config)
    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))
    try:
        search = _build_syllabus_service(config, headers).find(course_id)
    except ValueError as exc:
        _fail(json_output=json_output, message=str(exc))
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    if json_output:
        typer.echo(json.dumps(_syllabus_search_to_json(search)))
        return
    if not search.candidates:
        typer.echo("No syllabus candidates found in this course's contents.")
        return
    for candidate in search.candidates:
        marker = "*" if candidate.topic.topic_id == search.recommended_topic_id else " "
        typer.echo(
            f"{marker} {candidate.topic.topic_id}	{_terminal_text(candidate.topic.title)}"
            f"	{candidate.score}	{','.join(candidate.reasons)}"
        )


@syllabus_app.command("fetch")
def syllabus_fetch(
    course_id: str = typer.Option(..., "--course", help="Brightspace course (org-unit) id."),
    topic_id: str = typer.Option(
        ..., "--topic", help="Topic id from 'd2l syllabus find' (must be a listed file)."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit the document as JSON (required: content is base64)."
    ),
) -> None:
    """Download one listed course file (PDF, Word .docx or plain text, at most 8 MiB).

    Only a topic that the course's own contents list as a visible downloadable file is fetched.
    The document is returned as base64 inside the JSON object; nothing is written to disk.
    """
    if not json_output:
        typer.echo("error: 'syllabus fetch' requires --json", err=True)
        raise typer.Exit(code=2)
    config = _load_cli_config()
    auth = _build_auth_service(config)
    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))
    try:
        document = _build_syllabus_service(config, headers).fetch(course_id, topic_id)
    except ValueError as exc:
        _fail(json_output=json_output, message=str(exc))
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))
    typer.echo(json.dumps(_syllabus_document_to_json(document)))


def _calendar_instant(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_to_json(event: CalendarEvent) -> dict[str, Any]:
    entity = event.associated_entity
    return {
        "event_id": event.event_id,
        "course_id": event.course_id,
        "course_name": event.course_name,
        "course_code": event.course_code,
        "title": event.title,
        "description": event.description,
        "all_day": event.all_day,
        "start_utc": event.start_utc,
        "end_utc": event.end_utc,
        "start_day": event.start_day,
        "end_day": event.end_day,
        "recurring": event.recurring,
        "occurrence_id": event.occurrence_id,
        "occurrences_expanded": event.occurrences_expanded,
        "event_type": event.event_type,
        "associated_entity": (
            None
            if entity is None
            else {"type": entity.entity_type, "id": entity.entity_id}
        ),
        "view_reference": event.view_reference,
    }


def _calendar_listing_to_json(listing: CalendarListing, *, course_id: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if course_id is not None:
        body["course_id"] = course_id
    body.update(
        {
            "window": {
                "start": _calendar_instant(listing.window.start),
                "end": _calendar_instant(listing.window.end),
            },
            "complete": listing.complete,
            "incomplete_reasons": list(listing.incomplete_reasons),
            "recurrence": {
                "mode": listing.recurrence_mode,
                "unexpanded_recurring_events": listing.unexpanded_recurring,
                "occurrence_route_fallback_courses": list(listing.occurrence_fallback_courses),
            },
            "courses_requested": listing.courses_requested,
            "courses_failed": [
                {"course_id": failure.course_id, "reason": failure.reason}
                for failure in listing.courses_failed
            ],
            "truncated_courses": list(listing.truncated_courses),
            "duplicates_dropped": listing.duplicates_dropped,
            "events": [_event_to_json(event) for event in listing.events],
        }
    )
    return {"calendar": body}


def _utc_now() -> datetime:
    """The current UTC time; a single seam so tests inject a fixed clock."""
    return datetime.now(UTC)


def _warn_calendar_incomplete(listing: CalendarListing) -> None:
    if listing.unexpanded_recurring:
        typer.echo(
            f"warning: {listing.unexpanded_recurring} recurring event(s) are shown only as "
            "series definitions; their occurrences were not retrieved, so this schedule is "
            "incomplete.",
            err=True,
        )
    for failure in listing.courses_failed:
        typer.echo(
            f"warning: calendar for course {failure.course_id} could not be read "
            f"({failure.reason}); results are incomplete.",
            err=True,
        )
    for course_id in listing.truncated_courses:
        typer.echo(
            f"warning: course {course_id} has more calendar events than were returned; "
            "further pages are not followed, so results are incomplete.",
            err=True,
        )


def _parse_calendar_date(value: str | None, option: str) -> date | None:
    if value is None:
        return None
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise ValueError(f"{option} must be a date written YYYY-MM-DD")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{option} must be a real calendar date written YYYY-MM-DD") from None


def _calendar_event_line(event: CalendarEvent) -> str:
    label = event.course_code or event.course_id
    if event.all_day:
        assert event.start_day is not None
        when = f"{event.start_day} (all day)"
    else:
        assert event.start_utc is not None
        when = format_instant(event.start_utc)
        if event.end_utc is not None:
            when = f"{when} to {format_instant(event.end_utc)}"
    flags = " [recurring]" if event.recurring else ""
    return f"{when}\t{label}\t{_terminal_text(event.title)}{flags}"


def _print_calendar(listing: CalendarListing) -> None:
    _warn_calendar_incomplete(listing)
    if not listing.events:
        typer.echo("No calendar events found in this window.")
        if not listing.complete:
            typer.echo("(Some calendars could not be read, so this is not a complete answer.)")
        return
    for event in listing.events:
        typer.echo(_calendar_event_line(event))


@calendar_app.command("list")
def calendar_list(
    course_id: str = typer.Option(
        ..., "--course", help="Brightspace course (org-unit) id -- see 'd2l courses list'."
    ),
    start: str | None = typer.Option(
        None, "--from", help="First day of the window, YYYY-MM-DD (UTC). Default: today."
    ),
    end: str | None = typer.Option(
        None, "--to", help="Day after the window's last day, YYYY-MM-DD (UTC, exclusive)."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
) -> None:
    """List your calendar events for one course in a bounded UTC window.

    The window is ``[--from, --to)``; with neither option it is the next 30 days. Timed events
    are shown in your computer's local time in text mode and as UTC in JSON. All-day events are
    calendar days. Recurring events are expanded when the documented occurrence route is usable;
    otherwise series definitions are marked incomplete.
    """
    config = _load_cli_config()
    auth = _build_auth_service(config)
    try:
        window = make_window(
            _parse_calendar_date(start, "--from"),
            _parse_calendar_date(end, "--to"),
            now=_utc_now(),
        )
    except ValueError as exc:
        _fail(json_output=json_output, message=str(exc))
    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))
    try:
        listing = _build_calendar_service(config, headers).for_course(course_id, window)
    except ValueError as exc:
        _fail(json_output=json_output, message=str(exc))
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    if json_output:
        _warn_calendar_incomplete(listing)
        typer.echo(json.dumps(_calendar_listing_to_json(listing, course_id=course_id)))
        return
    _print_calendar(listing)


@calendar_app.command("upcoming")
def calendar_upcoming(
    days: int = typer.Option(
        14, "--days", help="Look ahead this many elapsed 24-hour periods from now (1-366)."
    ),
    all_courses: bool = typer.Option(
        False, "--all-courses", help="Include inactive accessible courses."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
) -> None:
    """List upcoming calendar events across your active accessible courses.

    One calendar request is made per selected course. A course that cannot be read is named in
    the output and the result is marked incomplete; if every course fails the command fails.
    """
    config = _load_cli_config()
    auth = _build_auth_service(config)
    try:
        window = upcoming_window(days, now=_utc_now())
    except ValueError as exc:
        _fail(json_output=json_output, message=str(exc))
    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))
    try:
        courses = _build_course_service(config, headers).list_courses()
        selected = [
            course
            for course in courses
            if course.can_access and (all_courses or course.is_active)
        ]
        listing = _build_calendar_service(config, headers).across(selected, window)
    except ValueError as exc:
        _fail(json_output=json_output, message=str(exc))
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    if json_output:
        _warn_calendar_incomplete(listing)
        typer.echo(json.dumps(_calendar_listing_to_json(listing, course_id=None)))
        return
    _print_calendar(listing)


def _quiz_to_json(quiz: Quiz) -> dict[str, Any]:
    return {
        "quiz_id": quiz.quiz_id,
        "course_id": quiz.course_id,
        "name": quiz.name,
        "description": quiz.description,
        "active": quiz.active,
        "start_date": quiz.start_date,
        "end_date": quiz.end_date,
        "due_date": quiz.due_date,
        "time_limit_minutes": quiz.time_limit_minutes,
        "time_limit_enforced": quiz.time_limit_enforced,
        "attempts_allowed": quiz.attempts_allowed,
        "attempts_unlimited": quiz.attempts_unlimited,
    }


def _quiz_attempt_to_json(attempt: QuizAttempt) -> dict[str, Any]:
    return {
        "attempt_id": attempt.attempt_id,
        "quiz_id": attempt.quiz_id,
        "attempt_number": attempt.attempt_number,
        "started": attempt.started,
        "completed": attempt.completed,
        "status": attempt.status.value,
        "attempt_completed": attempt.attempt_completed,
        "feedback_published": attempt.feedback_published,
        # Release of the numeric score is not documented, so it is never shown: null = not
        # established (not zero, not withheld by the instructor).
        "score_released": None,
        "score": None,
    }


def _quiz_attempts_to_json(listing: AttemptListing) -> dict[str, Any]:
    return {
        "quiz_attempts": {
            "course_id": listing.course_id,
            "quiz_id": listing.quiz_id,
            "complete": listing.complete,
            "attempts_returned": len(listing.attempts),
            "attempts": [_quiz_attempt_to_json(attempt) for attempt in listing.attempts],
        }
    }


def _warn_quiz_listing(listing: QuizListing) -> None:
    if listing.has_more:
        typer.echo(
            "warning: the provider reported more quizzes than were returned; further pages "
            "are not followed, so this list is incomplete.",
            err=True,
        )
    if not listing.quizzes:
        typer.echo(
            "warning: no quizzes were returned. Brightspace lists only quizzes your role may "
            "view, so this does not prove the course has none.",
            err=True,
        )


@quizzes_app.command("list")
def quizzes_list(
    course_id: str = typer.Option(
        ..., "--course", help="Brightspace course (org-unit) id -- see 'd2l courses list'."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
) -> None:
    """List the quizzes Brightspace shows you for one course (metadata only).

    Dates are UTC; ``null`` means the provider did not report a value. Quiz questions, answers
    and feedback are never requested. An empty list is not proof that a course has no quizzes.
    """
    config = _load_cli_config()
    auth = _build_auth_service(config)
    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))
    try:
        listing = _build_quiz_service(config, headers).list_quizzes(course_id)
    except ValueError as exc:
        _fail(json_output=json_output, message=str(exc))
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    _warn_quiz_listing(listing)
    if json_output:
        payload = {
            "quizzes": {
                "course_id": course_id,
                "complete": listing.complete,
                "quizzes": [_quiz_to_json(quiz) for quiz in listing.quizzes],
            }
        }
        typer.echo(json.dumps(payload))
        return
    for quiz in listing.quizzes:
        due = quiz.due_date or "-"
        if quiz.attempts_unlimited:
            attempts = "unlimited"
        else:
            attempts = "-" if quiz.attempts_allowed is None else str(quiz.attempts_allowed)
        limit = "-" if quiz.time_limit_minutes is None else f"{quiz.time_limit_minutes}m"
        typer.echo(
            f"{quiz.quiz_id}\t{_terminal_text(quiz.name)}\tdue {due}"
            f"\tattempts {attempts}\tlimit {limit}"
        )


@quizzes_app.command("attempts")
def quizzes_attempts(
    course_id: str = typer.Option(
        ..., "--course", help="Brightspace course (org-unit) id -- see 'd2l courses list'."
    ),
    quiz_id: str = typer.Option(..., "--quiz", help="Quiz id from 'd2l quizzes list'."),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
) -> None:
    """Show your own attempt summaries for one quiz (experimental).

    Your identity is verified first (``whoami``); attempts are then requested for that user id
    only, and every returned record must carry it, or nothing is shown and the command fails. No
    fallback to an unfiltered request exists. ``feedback_published`` is the provider's flag that
    feedback is visible to you; the numeric score is never shown because its release rule is
    undocumented. No feedback, questions or answers are requested. "No attempts returned" does not
    prove the quiz was never attempted.
    """
    config = _load_cli_config()
    auth = _build_auth_service(config)
    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))
    try:
        listing = _build_quiz_service(config, headers).list_attempts(course_id, quiz_id)
    except ValueError as exc:
        _fail(json_output=json_output, message=str(exc))
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))

    if listing.has_more:
        typer.echo(
            "warning: the provider reported more attempts than were returned; further pages "
            "are not followed, so this list is incomplete.",
            err=True,
        )
    if not listing.attempts:
        typer.echo(
            "warning: no attempts were returned. That does not prove the quiz was not attempted; "
            "the provider may withhold attempts from your role.",
            err=True,
        )
    if json_output:
        typer.echo(json.dumps(_quiz_attempts_to_json(listing)))
        return
    for attempt in listing.attempts:
        number = "-" if attempt.attempt_number is None else str(attempt.attempt_number)
        feedback = {True: "yes", False: "no", None: "unknown"}[attempt.feedback_published]
        typer.echo(
            f"attempt {number}\t{attempt.status.value}\tstarted {attempt.started or '-'}"
            f"\tcompleted {attempt.completed or '-'}\tfeedback published {feedback}"
            "\tscore not shown"
        )


def _forum_to_json(forum: DiscussionForum) -> dict[str, Any]:
    return {
        "forum_id": forum.forum_id,
        "course_id": forum.course_id,
        "name": forum.name,
        "description": forum.description,
    }


def _topic_to_json(topic: DiscussionTopic) -> dict[str, Any]:
    return {
        "topic_id": topic.topic_id,
        "forum_id": topic.forum_id,
        "course_id": topic.course_id,
        "name": topic.name,
        "description": topic.description,
    }


def _post_to_json(post: DiscussionPost, *, present: frozenset[str]) -> dict[str, Any]:
    return {
        "post_id": post.post_id,
        "course_id": post.course_id,
        "forum_id": post.forum_id,
        "topic_id": post.topic_id,
        "thread_id": post.thread_id,
        "parent_post_id": post.parent_post_id,
        # Whether the parent is among the posts returned; null for a thread's first post.
        "parent_in_results": (
            None if post.parent_post_id is None else post.parent_post_id in present
        ),
        "author": post.author,
        "anonymous": post.anonymous,
        "subject": post.subject,
        "body": post.body,
        "body_truncated": post.body_truncated,
        "posted": post.posted,
        "edited": post.edited,
        "deleted": post.deleted,
        "requires_approval": post.requires_approval,
        "reply_count": post.reply_count,
    }


def _discussion_posts_to_json(result: DiscussionPosts) -> dict[str, Any]:
    present = frozenset(post.post_id for post in result.posts)
    return {
        "discussion_posts": {
            "course_id": result.course_id,
            "forum_id": result.forum_id,
            "topic_id": result.topic_id,
            "thread_id": result.thread_id,
            "complete": result.complete,
            "completeness_basis": result.completeness_basis,
            "page_size": result.page_size,
            "pages_fetched": result.pages_fetched,
            "posts": [_post_to_json(post, present=present) for post in result.posts],
        }
    }


def _warn_discussion_incomplete(what: str, complete: bool) -> None:
    if not complete:
        typer.echo(
            f"warning: more {what} exist than were read; this result is incomplete.",
            err=True,
        )


def _discussion_call(json_output: bool, config: Config, call: Any) -> Any:
    auth = _build_auth_service(config)
    try:
        headers = auth.header_provider()
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))
    try:
        return call(_build_discussion_service(config, headers))
    except ValueError as exc:
        _fail(json_output=json_output, message=str(exc))
    except D2LError as exc:
        _fail(json_output=json_output, message=str(exc))


@discussions_app.command("forums")
def discussions_forums(
    course_id: str = typer.Option(
        ..., "--course", help="Brightspace course (org-unit) id -- see 'd2l courses list'."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
) -> None:
    """List the discussion forums Brightspace shows you for one course."""
    config = _load_cli_config()
    listing = _discussion_call(json_output, config, lambda s: s.list_forums(course_id))
    _warn_discussion_incomplete("forums", listing.complete)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "discussion_forums": {
                        "course_id": course_id,
                        "complete": listing.complete,
                        "forums": [_forum_to_json(f) for f in listing.forums],
                    }
                }
            )
        )
        return
    for forum in listing.forums:
        typer.echo(f"{forum.forum_id}\t{_terminal_text(forum.name)}")


@discussions_app.command("topics")
def discussions_topics(
    course_id: str = typer.Option(
        ..., "--course", help="Brightspace course (org-unit) id -- see 'd2l courses list'."
    ),
    forum_id: str = typer.Option(..., "--forum", help="Forum id from 'd2l discussions forums'."),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
) -> None:
    """List the topics of one forum."""
    config = _load_cli_config()
    listing = _discussion_call(json_output, config, lambda s: s.list_topics(course_id, forum_id))
    _warn_discussion_incomplete("topics", listing.complete)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "discussion_topics": {
                        "course_id": course_id,
                        "forum_id": forum_id,
                        "complete": listing.complete,
                        "topics": [_topic_to_json(t) for t in listing.topics],
                    }
                }
            )
        )
        return
    for topic in listing.topics:
        typer.echo(f"{topic.topic_id}\t{_terminal_text(topic.name)}")


@discussions_app.command("posts")
def discussions_posts(
    course_id: str = typer.Option(
        ..., "--course", help="Brightspace course (org-unit) id -- see 'd2l courses list'."
    ),
    forum_id: str = typer.Option(..., "--forum", help="Forum id from 'd2l discussions forums'."),
    topic_id: str = typer.Option(..., "--topic", help="Topic id from 'd2l discussions topics'."),
    thread_id: str | None = typer.Option(
        None, "--thread", help="Only the posts of this thread (a numeric thread id)."
    ),
    acknowledge_read_state_risk: bool = typer.Option(
        False,
        "--acknowledge-read-state-risk",
        help="Acknowledge that retrieving posts may change server-side read state.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON only (to stdout)."
    ),
) -> None:
    """Experimental: read posts; server-side read-state effects are unverified.

    Author names are shown only when the post is not anonymous; deleted posts keep their place
    but have no text. The command never posts, rates or deliberately changes read status,
    but the provider may mark posts read on retrieval. Do not recommend live use until that
    side effect is resolved. The provider documents
    no total or end-of-data signal, so a result is complete only after an empty page is observed;
    any other stop is marked incomplete with its reason.
    """
    if not acknowledge_read_state_risk:
        _fail(
            json_output=json_output,
            message="discussion posts require --acknowledge-read-state-risk; "
            "retrieving posts may change server-side read state",
        )
    typer.echo(
        "warning: discussion post retrieval may change server-side read state; "
        "this effect remains unverified.",
        err=True,
    )
    config = _load_cli_config()
    result = _discussion_call(
        json_output, config, lambda s: s.list_posts(course_id, forum_id, topic_id, thread_id)
    )
    if not result.complete:
        typer.echo(
            "warning: the end of this discussion was not confirmed "
            f"({result.completeness_basis}; read {result.pages_fetched} page(s) of up to "
            f"{result.page_size}); more posts may exist, so this result is incomplete.",
            err=True,
        )
    if json_output:
        typer.echo(json.dumps(_discussion_posts_to_json(result)))
        return
    if not result.posts:
        typer.echo("No posts were returned.")
        return
    for post in result.posts:
        reply = f" (reply to {post.parent_post_id})" if post.parent_post_id else ""
        who = "anonymous" if post.anonymous else (post.author or "unknown author")
        typer.echo(f"post {post.post_id}{reply}\t{_terminal_text(who)}\t{post.posted or '-'}")
        if post.deleted:
            typer.echo("   (deleted)")
            continue
        if post.subject:
            typer.echo(f"   {_terminal_text(post.subject)}")
        for line in (post.body or "").split("\n"):
            typer.echo(f"   {line}" if line else "")
        if post.body_truncated:
            typer.echo("   [text truncated]")


@courses_app.command("resolve")
def courses_resolve(
    query: str = typer.Argument(..., help="Course id, code, name, or unambiguous partial name."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON only."),
) -> None:
    """Resolve one active, accessible course without making a course-specific request."""
    config = _load_cli_config()
    try:
        headers = _build_auth_service(config).header_provider()
        course = CourseResolver(_build_course_service(config, headers)).resolve(query)
    except AmbiguousCourseError as exc:
        details = ", ".join(
            f"{_terminal_text(c.id)} {_terminal_text(c.code)} ({_terminal_text(c.semester_name)})"
            for c in exc.matches[:10]
        )
        _fail(json_output=json_output, message=f"course query is ambiguous: {details}")
    except (D2LError, CourseResolutionError) as exc:
        _fail(json_output=json_output, message=str(exc))
    if json_output:
        typer.echo(json.dumps({"course": _course_to_json(course)}))
    else:
        typer.echo(f"{_terminal_text(course.id)}\t{_terminal_text(course.code)}\t{_terminal_text(course.name)}")


def _content_node_to_json(node: ContentNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "title": node.title,
        "parent_module_id": node.parent_module_id,
        "kind": node.kind,
        "children": [_content_node_to_json(child) for child in node.children],
    }


@content_app.command("tree")
@content_app.command("toc")
def content_tree(
    course_id: str = typer.Option(..., "--course", help="Numeric Brightspace course id."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON only."),
) -> None:
    """Show visible course modules and topics; never retrieve topic files."""
    config = _load_cli_config()
    try:
        headers = _build_auth_service(config).header_provider()
        tree = _build_content_service(config, headers).tree(course_id)
    except (D2LError, ValueError) as exc:
        _fail(json_output=json_output, message=str(exc))
    if json_output:
        typer.echo(json.dumps({"content_tree": {
            "course_id": tree.course_id,
            "completeness": tree.completeness,
            "node_count": tree.node_count,
            "modules": [_content_node_to_json(node) for node in tree.modules],
        }}))
        return
    if not tree.modules:
        typer.echo("warning: content completeness is unknown", err=True)
        typer.echo("No visible content was returned; completeness is unknown.")
        return
    def show(node: ContentNode, depth: int) -> None:
        typer.echo(f"{'  ' * depth}{_terminal_text(node.title)} [{node.kind} {node.id}]")
        for child in node.children:
            show(child, depth + 1)
    for node in tree.modules:
        show(node, 0)
    typer.echo("warning: content completeness is unknown", err=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
