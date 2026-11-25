"""Optional, privacy-safe subprocess adapter for snapshot JSON consumers."""

from __future__ import annotations

import json
import math
import subprocess
from typing import Any


class AgentSnapshotError(Exception):
    """Fixed failure code; never includes stdout, stderr, or student records."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def decode_snapshot_process(exit_code: int, stdout: str) -> dict[str, Any]:
    """Validate the minimum v1 envelope before an agent uses any records."""
    if not stdout.strip():
        raise AgentSnapshotError("command_failed" if exit_code else "malformed_json")
    try:
        document = json.loads(stdout)
    except (ValueError, TypeError):
        raise AgentSnapshotError("malformed_json") from None
    if not isinstance(document, dict):
        raise AgentSnapshotError("malformed_json")
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise AgentSnapshotError("unsupported_schema")
    if exit_code not in (0, 1):
        raise AgentSnapshotError("command_failed")
    courses = document.get("courses")
    requested = document.get("requested_sources")
    if (
        not isinstance(courses, list)
        or not isinstance(requested, list)
        or not requested
        or any(source not in ("assignments", "grades", "announcements") for source in requested)
        or type(document.get("partial")) is not bool
    ):
        raise AgentSnapshotError("invalid_envelope")
    failed = False
    for course in courses:
        if not isinstance(course, dict) or not isinstance(course.get("sources"), dict):
            raise AgentSnapshotError("invalid_envelope")
        for source in requested:
            state = course["sources"].get(source)
            if not isinstance(state, dict) or state.get("status") not in ("success", "failed"):
                raise AgentSnapshotError("invalid_envelope")
            failed |= state["status"] == "failed"
        for name in ("assignments", "grade_items", "announcements"):
            if not isinstance(course.get(name), list):
                raise AgentSnapshotError("invalid_envelope")
    if failed != document["partial"]:
        raise AgentSnapshotError("invalid_envelope")
    if exit_code == 1 and not failed:
        raise AgentSnapshotError("command_failed")
    return document


def run_snapshot(
    *, executable: str = "d2l", max_courses: int = 10, timeout_seconds: float = 120
) -> dict[str, Any]:
    """Run only the bounded snapshot CLI; authentication remains manual."""
    if not 1 <= max_courses <= 20 or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("invalid snapshot subprocess bound")
    try:
        completed = subprocess.run(  # noqa: S603 - fixed arguments, no shell
            [executable, "snapshot", "--json", "--max-courses", str(max_courses)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AgentSnapshotError("timeout") from None
    except OSError:
        raise AgentSnapshotError("command_unavailable") from None
    return decode_snapshot_process(completed.returncode, completed.stdout)
