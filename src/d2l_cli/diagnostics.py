"""Local-only diagnostics; never inspects session storage or the provider."""

from __future__ import annotations

import importlib.util
import platform
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from d2l_cli.client.errors import D2LError
from d2l_cli.config import load_config

_DEPENDENCIES = ("certifi", "httpx", "keyring", "playwright", "typer")
_CAPABILITIES = (
    "read_courses",
    "read_assignments",
    "read_displayed_grades",
    "read_announcements",
    "read_academic_overview",
    "get_only_client",
)
_LIMITATIONS = (
    "session_presence_not_checked",
    "provider_compatibility_not_verified",
    "grade_and_announcement_completeness_unknown",
    "assignment_dates_have_no_verified_timezone",
)


def diagnostics_report() -> dict[str, Any]:
    """Return a stable report from package metadata, imports, and non-secret config."""
    try:
        app_version = version("d2l-cli")
    except PackageNotFoundError:
        app_version = "unknown"

    try:
        load_config()  # Only validates D2L_CLI_BASE_URL; no credential access.
        valid = True
    except D2LError:
        valid = False

    return {
        "schema_version": 1,
        "application_version": app_version,
        "python_version": platform.python_version(),
        "configuration_valid": valid,
        "configuration_error_code": None if valid else "invalid_base_url",
        "dependencies": {
            name: importlib.util.find_spec(name) is not None for name in _DEPENDENCIES
        },
        "local_capabilities": list(_CAPABILITIES),
        "session_presence": "not_checked",
        "provider_verification": "not_performed",
        "known_limitations": list(_LIMITATIONS),
    }
