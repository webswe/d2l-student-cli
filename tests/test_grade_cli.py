from __future__ import annotations

import json
from collections.abc import Mapping

import httpx
import pytest
import respx
from typer.testing import CliRunner

import d2l_cli.cli as cli_module
from d2l_cli.client.errors import (
    AuthExpiredError,
    MalformedResponseError,
    NetworkUnavailableError,
    NotAuthenticatedError,
    PermissionDeniedError,
    ProviderEndpointChangedError,
    UnexpectedProviderError,
)
from d2l_cli.models.grade import FeedbackAvailability, GradeItem, GradeListPage

runner = CliRunner()

_COURSE_ID = "620000"
_ITEMS = [
    GradeItem(
        course_id=_COURSE_ID,
        label="Practice Set Alpha",
        displayed_points="7 / 11",
        displayed_grade="Custom mark",
        displayed_weight=None,
        feedback_availability=FeedbackAvailability.NONE,
    ),
    GradeItem(
        course_id=_COURSE_ID,
        label="Laboratory Beta",
        displayed_points=None,
        displayed_grade=None,
        displayed_weight="12.5%",
        feedback_availability=FeedbackAvailability.PRESENT,
    ),
    GradeItem(
        course_id=_COURSE_ID,
        label="Reflection Gamma",
        displayed_points="0",
        displayed_grade="Pending review",
        displayed_weight="0%",
        feedback_availability=FeedbackAvailability.UNKNOWN,
    ),
]


class _AuthService:
    def __init__(self, headers: Mapping[str, str] | Exception) -> None:
        self._headers = headers

    def header_provider(self) -> Mapping[str, str]:
        if isinstance(self._headers, Exception):
            raise self._headers
        return self._headers


class _GradeService:
    def __init__(
        self,
        items: list[GradeItem] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._items = [] if items is None else items
        self._error = error
        self.requested_course_ids: list[str] = []

    def get_grade_list(self, course_id: str) -> GradeListPage:
        self.requested_course_ids.append(course_id)
        if self._error is not None:
            raise self._error
        return GradeListPage(
            items=tuple(self._items),
            category_row_count=0,
            header_effective_width=4,
            has_weight_column=any(item.displayed_weight is not None for item in self._items),
        )


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
    service: _GradeService | None = None,
    auth_error: Exception | None = None,
):
    headers: Mapping[str, str] | Exception = (
        auth_error if auth_error is not None else {"Cookie": "fictional-session"}
    )
    fake_service = service or _GradeService(list(_ITEMS))
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: _AuthService(headers))
    monkeypatch.setattr(
        cli_module,
        "_build_grade_service",
        lambda config, provided_headers: fake_service,
    )
    result = runner.invoke(
        cli_module.app,
        ["grades", "list", "--course", _COURSE_ID, *extra],
    )
    return result, fake_service


def test_grades_help_and_command_registration() -> None:
    top_level = runner.invoke(cli_module.app, ["--help"])
    grades = runner.invoke(cli_module.app, ["grades", "--help"])

    assert top_level.exit_code == 0
    assert "grades" in top_level.stdout
    assert grades.exit_code == 0
    assert "list" in grades.stdout


def test_grades_list_requires_course_option() -> None:
    result = runner.invoke(cli_module.app, ["grades", "list"])

    assert result.exit_code != 0
    assert "--course" in result.stderr or "--course" in result.stdout


def test_grades_list_text_output_is_display_only_and_ordered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, service = _invoke(monkeypatch)

    assert result.exit_code == 0
    assert service.requested_course_ids == [_COURSE_ID]
    lines = result.stdout.splitlines()
    assert lines[0] == "Grade item\tPoints\tGrade\tWeight\tFeedback"
    assert lines[1:] == [
        "Practice Set Alpha\t7 / 11\tCustom mark\t-\tnone",
        "Laboratory Beta\t-\t-\t12.5%\tpresent",
        "Reflection Gamma\t0\tPending review\t0%\tunknown",
    ]
    assert "63.6" not in result.stdout
    assert result.stderr.count("completeness cannot be verified") == 1
    assert "unknown" in result.stderr


def test_grades_text_output_strips_terminal_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = GradeItem(
        course_id=_COURSE_ID,
        label="Lab\x1b[2J Alpha",
        displayed_points="7\x1b]0;FICTIONAL\x07 / 11",
        displayed_grade="Custom\u202e mark",
        displayed_weight="10\x9b31m%",
        feedback_availability=FeedbackAvailability.NONE,
    )

    result, _service = _invoke(monkeypatch, service=_GradeService([item]))

    assert result.exit_code == 0
    assert result.stdout.splitlines()[1] == "Lab Alpha\t7 / 11\tCustom mark\t10%\tnone"
    assert "\x1b" not in result.stdout
    assert "\x9b" not in result.stdout
    assert "\u202e" not in result.stdout


def test_grades_list_json_contract_preserves_nulls_and_display_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _service = _invoke(monkeypatch, "--json")

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "grade_items": [
            {
                "course_id": _COURSE_ID,
                "label": "Practice Set Alpha",
                "displayed_points": "7 / 11",
                "displayed_grade": "Custom mark",
                "displayed_weight": None,
                "feedback_availability": "none",
            },
            {
                "course_id": _COURSE_ID,
                "label": "Laboratory Beta",
                "displayed_points": None,
                "displayed_grade": None,
                "displayed_weight": "12.5%",
                "feedback_availability": "present",
            },
            {
                "course_id": _COURSE_ID,
                "label": "Reflection Gamma",
                "displayed_points": "0",
                "displayed_grade": "Pending review",
                "displayed_weight": "0%",
                "feedback_availability": "unknown",
            },
        ]
    }


def test_grades_list_json_completeness_is_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default, _service = _invoke(monkeypatch, "--json")
    explicit, _service = _invoke(monkeypatch, "--json", "--include-completeness")

    default_payload = json.loads(default.stdout)
    explicit_payload = json.loads(explicit.stdout)
    assert set(default_payload) == {"grade_items"}
    assert explicit_payload == {**default_payload, "completeness": "unknown"}
    assert default.stderr == ""
    assert explicit.stderr == ""


def test_grades_list_empty_results_are_successful(monkeypatch: pytest.MonkeyPatch) -> None:
    text, _service = _invoke(monkeypatch, service=_GradeService([]))
    as_json, _service = _invoke(monkeypatch, "--json", service=_GradeService([]))
    with_completeness, _service = _invoke(
        monkeypatch,
        "--json",
        "--include-completeness",
        service=_GradeService([]),
    )

    assert text.exit_code == 0
    assert text.stdout.strip() == "No visible grade items found."
    assert text.stderr.count("completeness cannot be verified") == 1
    assert as_json.exit_code == 0
    assert json.loads(as_json.stdout) == {"grade_items": []}
    assert as_json.stderr == ""
    assert with_completeness.exit_code == 0
    assert json.loads(with_completeness.stdout) == {
        "grade_items": [],
        "completeness": "unknown",
    }
    assert with_completeness.stderr == ""


def test_grades_list_invalid_course_id_is_safe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _GradeService(
        error=ValueError("course_id must be a non-empty numeric Brightspace org-unit id")
    )
    result, _service = _invoke(monkeypatch, "--json", service=service)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "org-unit id" in result.stderr
    assert _COURSE_ID not in result.stderr


def test_grades_list_authentication_failure_keeps_json_stdout_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _service = _invoke(
        monkeypatch,
        "--json",
        auth_error=NotAuthenticatedError("no stored session"),
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "no stored session" in result.stderr
    assert "completeness cannot be verified" not in result.stderr


@pytest.mark.parametrize(
    "error",
    [
        AuthExpiredError("stored Brightspace session expired; run 'd2l auth login' again"),
        PermissionDeniedError("provider denied access to this resource"),
        MalformedResponseError("provider response had an unexpected content type"),
        ProviderEndpointChangedError("Grades page has unsupported table structure (row width)"),
        ProviderEndpointChangedError(
            "Grades page has unsupported table structure (unexpected redirect)"
        ),
        NetworkUnavailableError("could not connect to provider"),
        UnexpectedProviderError("provider returned unexpected status 418"),
    ],
)
def test_grades_list_provider_failures_are_nonzero_and_not_empty_results(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    result, _service = _invoke(monkeypatch, "--json", service=_GradeService(error=error))

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    assert "No visible grade items" not in result.stderr
    assert "completeness cannot be verified" not in result.stderr


def test_grades_list_course_mismatch_fails_without_leaking_item_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mismatched = GradeItem(
        course_id="999999",
        label="CONFIDENTIAL ITEM LABEL",
        displayed_points="SECRET SCORE",
        displayed_grade="SECRET GRADE",
        displayed_weight=None,
        feedback_availability=FeedbackAvailability.UNKNOWN,
    )
    result, _service = _invoke(
        monkeypatch,
        "--json",
        service=_GradeService([mismatched]),
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "requested course" in result.stderr
    assert "completeness cannot be verified" not in result.stderr
    for secret in ("999999", "CONFIDENTIAL", "SECRET", _COURSE_ID):
        assert secret not in result.stderr


@respx.mock
def test_grades_completeness_adds_no_http_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_build_auth_service",
        lambda config: _AuthService({"Cookie": "fictional-session"}),
    )
    base_url = "https://kennesaw.view.usg.edu"
    index = respx.get(f"{base_url}/d2l/lms/grades/index.d2l?ou={_COURSE_ID}").mock(
        return_value=httpx.Response(
            302,
            headers={
                "location": f"/d2l/lms/grades/my_grades/main.d2l?ou={_COURSE_ID}"
            },
        )
    )
    final = respx.get(
        f"{base_url}/d2l/lms/grades/my_grades/main.d2l?ou={_COURSE_ID}"
    ).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=UTF-8"},
            text="""
            <d2l-table-wrapper><table>
              <tr><th>Grade Item</th><th>Points</th><th>Grade</th><th>Feedback</th></tr>
              <tr><th>Fictional Activity</th><td>3 / 5</td><td>Shown</td><td></td></tr>
            </table></d2l-table-wrapper>
            """,
        )
    )

    result = runner.invoke(
        cli_module.app,
        [
            "grades",
            "list",
            "--course",
            _COURSE_ID,
            "--json",
            "--include-completeness",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["completeness"] == "unknown"
    assert result.stderr == ""
    assert index.call_count == 1
    assert final.call_count == 1
    assert len(respx.calls) == 2
