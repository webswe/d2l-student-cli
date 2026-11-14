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
from d2l_cli.models.announcement import Announcement, AnnouncementListPage

runner = CliRunner()

_COURSE_ID = "620000"
_ITEMS = [
    Announcement(
        course_id=_COURSE_ID,
        announcement_id="501",
        title="Fictional Update Alpha",
        body="First paragraph.\n\n- First item\n- Second item",
        detail_reference="/d2l/le/news/620000/501/view",
    ),
    Announcement(
        course_id=_COURSE_ID,
        announcement_id="502",
        title="Fictional Update Beta",
        body=None,
        detail_reference="/d2l/le/news/620000/502/view",
    ),
]


class _AuthService:
    def __init__(self, headers: Mapping[str, str] | Exception) -> None:
        self._headers = headers

    def header_provider(self) -> Mapping[str, str]:
        if isinstance(self._headers, Exception):
            raise self._headers
        return self._headers


class _AnnouncementService:
    def __init__(
        self,
        items: list[Announcement] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._items = [] if items is None else items
        self._error = error
        self.requested_course_ids: list[str] = []

    def get_announcement_list(self, course_id: str) -> AnnouncementListPage:
        self.requested_course_ids.append(course_id)
        if self._error is not None:
            raise self._error
        return AnnouncementListPage(tuple(self._items))


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
    service: _AnnouncementService | None = None,
    auth_error: Exception | None = None,
    course_id: str = _COURSE_ID,
):
    headers: Mapping[str, str] | Exception = (
        auth_error if auth_error is not None else {"Cookie": "fictional-session"}
    )
    fake_service = service or _AnnouncementService(list(_ITEMS))
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: _AuthService(headers))
    monkeypatch.setattr(
        cli_module,
        "_build_announcement_service",
        lambda config, provided_headers: fake_service,
    )
    result = runner.invoke(
        cli_module.app,
        ["announcements", "list", "--course", course_id, *extra],
    )
    return result, fake_service


def test_announcements_help_and_command_registration() -> None:
    top_level = runner.invoke(cli_module.app, ["--help"])
    group = runner.invoke(cli_module.app, ["announcements", "--help"])
    listing = runner.invoke(cli_module.app, ["announcements", "list", "--help"])

    assert top_level.exit_code == 0
    assert "announcements" in top_level.stdout
    assert group.exit_code == 0
    assert "list" in group.stdout
    assert listing.exit_code == 0
    for option in ("--course", "--json", "--details", "--include-completeness"):
        assert option in listing.stdout


def test_announcements_list_requires_course_option() -> None:
    result = runner.invoke(cli_module.app, ["announcements", "list"])

    assert result.exit_code != 0
    assert "--course" in result.stderr or "--course" in result.stdout


def test_default_text_listing_is_compact_ordered_and_warns_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, service = _invoke(monkeypatch)

    assert result.exit_code == 0
    assert service.requested_course_ids == [_COURSE_ID]
    assert result.stdout.splitlines() == [
        "1. Fictional Update Alpha",
        "   First paragraph. - First item - Second item",
        "2. Fictional Update Beta",
        "   (empty body)",
    ]
    assert result.stderr.count("completeness cannot be verified") == 1
    assert "unknown" in result.stderr


def test_detailed_text_preserves_body_boundaries_and_empty_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _service = _invoke(monkeypatch, "--details")

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "1. Fictional Update Alpha",
        "   First paragraph.",
        "",
        "   - First item",
        "   - Second item",
        "",
        "2. Fictional Update Beta",
        "   (empty body)",
    ]
    assert result.stderr.count("completeness cannot be verified") == 1


def test_text_output_flattens_titles_previews_and_terminal_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_body = "A" * 130 + "\nPRIVATE TAIL"
    unsafe = Announcement(
        course_id=_COURSE_ID,
        announcement_id="503",
        title="Line one\nLine\ttwo \x1b[31mred\x1b[0m \x1b]8;;https://invalid\x07link\x1b]8;;\x07",
        body="Column\tone\r\nColumn two \x9b32mgreen\x9b0m",
        detail_reference="/d2l/le/news/620000/503/view",
    )
    long_item = Announcement(
        course_id=_COURSE_ID,
        announcement_id="504",
        title="Long fictional update",
        body=long_body,
        detail_reference="/d2l/le/news/620000/504/view",
    )

    result, _service = _invoke(
        monkeypatch,
        service=_AnnouncementService([unsafe, long_item]),
    )

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines[0] == "1. Line one Line two red link"
    assert lines[1] == "   Column one Column two green"
    assert lines[2] == "2. Long fictional update"
    assert lines[3] == "   " + "A" * 117 + "..."
    assert "PRIVATE TAIL" not in result.stdout
    for control in ("\x1b", "\x9b", "\r", "\t"):
        assert control not in result.stdout


def test_json_contract_and_optional_completeness_are_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default, _service = _invoke(monkeypatch, "--json")
    explicit, _service = _invoke(monkeypatch, "--json", "--include-completeness")
    with_details, _service = _invoke(monkeypatch, "--json", "--details")

    expected = {
        "announcements": [
            {
                "course_id": _COURSE_ID,
                "announcement_id": "501",
                "title": "Fictional Update Alpha",
                "body": "First paragraph.\n\n- First item\n- Second item",
                "detail_reference": "/d2l/le/news/620000/501/view",
            },
            {
                "course_id": _COURSE_ID,
                "announcement_id": "502",
                "title": "Fictional Update Beta",
                "body": None,
                "detail_reference": "/d2l/le/news/620000/502/view",
            },
        ]
    }
    assert default.exit_code == 0
    assert json.loads(default.stdout) == expected
    assert set(json.loads(default.stdout)) == {"announcements"}
    assert default.stderr == ""
    assert json.loads(explicit.stdout) == {**expected, "completeness": "unknown"}
    assert explicit.stderr == ""
    assert json.loads(with_details.stdout) == expected
    assert with_details.stderr == ""


def test_human_preview_does_not_truncate_json_announcement_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = "Fictional opening 🚀\n" + "A" * 240 + "\nFictional final marker"
    item = Announcement(
        course_id=_COURSE_ID,
        announcement_id="503",
        title="Long fictional update",
        body=body,
        detail_reference="/d2l/le/news/620000/503/view",
    )
    service = _AnnouncementService([item])

    preview, _ = _invoke(monkeypatch, service=service)
    detailed, _ = _invoke(monkeypatch, "--details", service=service)
    default_json, _ = _invoke(monkeypatch, "--json", service=service)
    detailed_json, _ = _invoke(monkeypatch, "--json", "--details", service=service)

    assert preview.exit_code == detailed.exit_code == 0
    assert "Fictional final marker" not in preview.stdout
    assert "Fictional final marker" in detailed.stdout
    assert preview.stderr.count("completeness cannot be verified") == 1
    assert detailed.stderr.count("completeness cannot be verified") == 1

    expected = {
        "announcements": [
            {
                "course_id": _COURSE_ID,
                "announcement_id": "503",
                "title": "Long fictional update",
                "body": body,
                "detail_reference": "/d2l/le/news/620000/503/view",
            }
        ]
    }
    assert default_json.exit_code == detailed_json.exit_code == 0
    assert json.loads(default_json.stdout) == expected
    assert json.loads(detailed_json.stdout) == expected
    assert default_json.stderr == detailed_json.stderr == ""


def test_empty_results_are_successful_without_duplicate_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text, _service = _invoke(monkeypatch, service=_AnnouncementService([]))
    as_json, _service = _invoke(monkeypatch, "--json", service=_AnnouncementService([]))

    assert text.exit_code == 0
    assert text.stdout.strip() == "No announcements found."
    assert text.stderr.count("completeness cannot be verified") == 1
    assert as_json.exit_code == 0
    assert json.loads(as_json.stdout) == {"announcements": []}
    assert as_json.stderr == ""


def test_invalid_course_id_is_a_safe_nonzero_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _AnnouncementService(
        error=ValueError("course_id must be a non-empty numeric Brightspace org-unit id")
    )
    result, used_service = _invoke(
        monkeypatch,
        "--json",
        service=service,
        course_id="not-numeric",
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "org-unit id" in result.stderr
    assert "not-numeric" not in result.stderr
    assert used_service.requested_course_ids == ["not-numeric"]


def test_authentication_failure_keeps_json_stdout_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, service = _invoke(
        monkeypatch,
        "--json",
        auth_error=NotAuthenticatedError("no stored session"),
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "no stored session" in result.stderr
    assert service.requested_course_ids == []
    assert "completeness cannot be verified" not in result.stderr


@pytest.mark.parametrize(
    "error",
    [
        AuthExpiredError("stored Brightspace session expired; run 'd2l auth login' again"),
        PermissionDeniedError("provider denied access to this resource"),
        MalformedResponseError("provider response had an unexpected content type"),
        ProviderEndpointChangedError(
            "Announcements page structure is unsupported (ANNOUNCEMENTS_ITEM_ROW_GEOMETRY)"
        ),
        NetworkUnavailableError("could not connect to provider"),
        UnexpectedProviderError("provider returned unexpected status 418"),
    ],
)
def test_provider_and_structural_failures_are_nonzero_not_empty_success(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    result, _service = _invoke(
        monkeypatch,
        "--json",
        service=_AnnouncementService(error=error),
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    assert "No announcements" not in result.stderr
    assert "completeness cannot be verified" not in result.stderr


def test_course_mismatch_fails_without_leaking_announcement_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mismatched = Announcement(
        course_id="999999",
        announcement_id="PRIVATE-501",
        title="CONFIDENTIAL FICTIONAL TITLE",
        body="SECRET FICTIONAL BODY",
        detail_reference="/private/reference",
    )
    result, _service = _invoke(
        monkeypatch,
        "--json",
        service=_AnnouncementService([mismatched]),
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "requested course" in result.stderr
    for private in ("999999", "PRIVATE", "CONFIDENTIAL", "SECRET", _COURSE_ID):
        assert private not in result.stderr


@respx.mock
def test_real_cli_sanitizes_html_and_makes_one_get_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_build_auth_service",
        lambda config: _AuthService({"Cookie": "fictional-session"}),
    )
    base_url = "https://kennesaw.view.usg.edu"
    route = respx.get(f"{base_url}/d2l/lms/news/main.d2l?ou={_COURSE_ID}").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=UTF-8"},
            text=f"""
            <form method="post" action="main.d2l?ou={_COURSE_ID}"><table>
              <tr><th><a href="/d2l/le/news/{_COURSE_ID}/501/view?ou={_COURSE_ID}">
                Fictional &amp; Safe</a></th><td>Unsupported date</td></tr>
              <tr><td colspan="2"><d2l-html-block
                html="&lt;p&gt;Visible &amp;amp; safe&lt;/p&gt;
                &lt;script&gt;PRIVATE_SCRIPT&lt;/script&gt;
                &lt;ul&gt;&lt;li&gt;List item&lt;/li&gt;&lt;/ul&gt;">
              </d2l-html-block></td></tr>
            </table></form>
            """,
        )
    )

    result = runner.invoke(
        cli_module.app,
        [
            "announcements",
            "list",
            "--course",
            _COURSE_ID,
            "--json",
            "--include-completeness",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "announcements": [
            {
                "course_id": _COURSE_ID,
                "announcement_id": "501",
                "title": "Fictional & Safe",
                "body": "Visible & safe\n\n- List item",
                "detail_reference": "/d2l/le/news/620000/501/view",
            }
        ],
        "completeness": "unknown",
    }
    assert "PRIVATE_SCRIPT" not in result.stdout
    assert "<p>" not in result.stdout
    assert result.stderr == ""
    assert route.call_count == 1
    assert len(respx.calls) == 1
    assert {call.request.method for call in respx.calls} == {"GET"}
    assert "/view" not in str(respx.calls[0].request.url)
