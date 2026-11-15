"""Security properties shared by the calendar, quiz and discussion commands.

These tests drive the real CLI, services, providers and HTTP client against a recording mock
transport, so they check what would actually be sent. All domains, ids and values are fictional;
no real credential store or institution is involved.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

import d2l_cli.cli as cli_module
from d2l_cli.models.course import Course

BASE = "https://example.brightspace.com"
OTHER = "https://evil.example.test"
COOKIE = "d2lSessionVal=FICTIONAL_ACTIVITY_COOKIE_SECRET"
SECRET = "FICTIONAL_ACTIVITY_COOKIE_SECRET"
MARKER = "FICTIONAL_BODY_MARKER_9F3A"
VERSIONS = [{"ProductCode": "le", "SupportedVersions": ["1.74", "1.82", "1.90"]}]

COMMANDS: list[tuple[str, ...]] = [
    ("calendar", "list", "--course", "1001", "--json"),
    ("calendar", "upcoming", "--json"),
    ("quizzes", "list", "--course", "1001", "--json"),
    ("quizzes", "attempts", "--course", "1001", "--quiz", "3003", "--json"),
    ("discussions", "forums", "--course", "1001", "--json"),
    ("discussions", "topics", "--course", "1001", "--forum", "4004", "--json"),
    ("discussions", "posts", "--course", "1001", "--forum", "4004", "--topic", "5005", "--json"),
]
FORBIDDEN_SEGMENTS = (
    "/questions",
    "/ReadStatus",
    "/Rating",
    "/Votes",
    "/Flag",
    "/Approval",
    "/attachments",
    "/access/",
    "/groupRestrictions",
    "/specialaccess",
)

runner = CliRunner()


class _Auth:
    def header_provider(self) -> dict[str, str]:
        return {"Cookie": COOKIE}


class _Courses:
    def list_courses(self) -> list[Course]:
        return [Course("1001", "C101", "Fictional Course", "Spring", True, True, False, None, None)]


class Recorder:
    """A mock transport that records every request and answers with canned JSON."""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond or self._default

    @staticmethod
    def _default(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/d2l/api/versions/":
            return httpx.Response(200, json=VERSIONS)
        if path.endswith("/calendar/events/myEvents/"):
            event = {
                "CalendarEventId": 1,
                "OrgUnitId": 1001,
                "Title": MARKER,
                "StartDateTime": "2099-01-01T10:00:00.000Z",
                "EndDateTime": None,
                "IsAllDayEvent": False,
            }
            return httpx.Response(200, json=[event])
        return httpx.Response(200, json=[])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    monkeypatch.setattr(cli_module, "load_config", lambda: SimpleNamespace(base_url=BASE))
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: _Auth())
    monkeypatch.setattr(cli_module, "_build_course_service", lambda config, headers: _Courses())
    recorder = Recorder()
    return recorder


def run(recorder: Recorder, *command: str) -> Any:
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=recorder)
        return runner.invoke(cli_module.app, list(command))


@pytest.mark.parametrize("command", COMMANDS, ids=" ".join)
def test_only_get_requests_to_the_configured_origin_carrying_only_the_session_cookie(
    stack: Recorder, command: tuple[str, ...]
) -> None:
    result = run(stack, *command)
    assert result.exit_code == 0, result.stderr
    assert stack.requests, "the command made no request"
    for request in stack.requests:
        assert request.method == "GET"
        assert (request.url.scheme, request.url.host) == ("https", "example.brightspace.com")
        assert request.url.path.startswith("/d2l/api/")
        assert request.headers["cookie"] == COOKIE
        assert not any(segment in request.url.path for segment in FORBIDDEN_SEGMENTS)
    assert json.loads(result.stdout)  # pure JSON on stdout


@pytest.mark.parametrize("command", COMMANDS, ids=" ".join)
def test_a_redirect_is_never_followed_and_nothing_reaches_another_origin(
    stack: Recorder, command: tuple[str, ...]
) -> None:
    def redirect(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/d2l/api/versions/":
            return httpx.Response(200, json=VERSIONS)
        return httpx.Response(302, headers={"location": f"{OTHER}/steal"})

    recorder = Recorder(redirect)
    result = run(recorder, *command)
    assert result.exit_code == 1 and result.stdout == ""
    assert all(r.url.host == "example.brightspace.com" for r in recorder.requests)
    assert not any("evil" in str(r.url) for r in recorder.requests)
    assert SECRET not in result.stderr and OTHER not in result.stderr


@pytest.mark.parametrize("command", COMMANDS, ids=" ".join)
@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_provider_errors_are_clean_and_never_echo_the_response_body_or_cookie(
    stack: Recorder, command: tuple[str, ...], status: int
) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/d2l/api/versions/":
            return httpx.Response(200, json=VERSIONS)
        return httpx.Response(status, text=f"{MARKER} {COOKIE}")

    result = run(Recorder(failing), *command)
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr.startswith("error: ") or "warning" in result.stderr
    for text in (result.stdout, result.stderr):
        assert MARKER not in text and SECRET not in text


@pytest.mark.parametrize("command", COMMANDS[:1] + COMMANDS[2:], ids=" ".join)
def test_malformed_json_and_html_error_pages_are_unsupported_responses_not_data(
    stack: Recorder, command: tuple[str, ...]
) -> None:
    def junk(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/d2l/api/versions/":
            return httpx.Response(200, json=VERSIONS)
        return httpx.Response(
            200, text=f"<html>{MARKER}</html>", headers={"content-type": "text/html"}
        )

    result = run(Recorder(junk), *command)
    assert result.exit_code == 1 and result.stdout == "" and MARKER not in result.stderr


@pytest.mark.parametrize("command", COMMANDS, ids=" ".join)
def test_response_bodies_and_cookies_are_never_logged(
    stack: Recorder, command: tuple[str, ...], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    result = run(stack, *command)
    assert result.exit_code == 0
    assert MARKER not in caplog.text and SECRET not in caplog.text


BAD_IDS: list[tuple[str, ...]] = [
    ("calendar", "list", "--course", "10/../01", "--json"),
    ("quizzes", "list", "--course", "abc", "--json"),
    ("quizzes", "attempts", "--course", "1001", "--quiz", "1;DROP", "--json"),
    ("discussions", "forums", "--course", "", "--json"),
    ("discussions", "topics", "--course", "1001", "--forum", "%2e%2e", "--json"),
    ("discussions", "posts", "--course", "1001", "--forum", "4004", "--topic", "5 5", "--json"),
    (
        "discussions",
        "posts",
        "--course",
        "1001",
        "--forum",
        "4004",
        "--topic",
        "55",
        "--thread",
        "x",
        "--json",
    ),
]


@pytest.mark.parametrize("command", BAD_IDS, ids=" ".join)
def test_invalid_ids_are_rejected_before_any_request_is_made(
    stack: Recorder, command: tuple[str, ...]
) -> None:
    result = run(stack, *command)
    assert result.exit_code == 1 and result.stdout == ""
    assert stack.requests == []
    assert "numeric" in result.stderr


def test_the_http_client_still_has_no_write_capable_method() -> None:
    from d2l_cli.client.http import D2LHttpClient

    public = {name for name in dir(D2LHttpClient) if not name.startswith("_")}
    assert not public & {"post", "put", "patch", "delete", "request", "send"}
