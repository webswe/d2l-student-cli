"""Quizzes: metadata parsing, attempt privacy, permission handling, and the CLI.

All data is fictional. Nothing here contacts a real institution or reads a real credential store.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

import d2l_cli.cli as cli_module
from d2l_cli.client.errors import (
    IdentityUnverifiedError,
    NotAuthenticatedError,
    PermissionDeniedError,
    ProviderEndpointChangedError,
    RateLimitedError,
)
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.models.quiz import AttemptListing, AttemptStatus, QuizListing
from d2l_cli.providers.brightspace_identity import (
    IDENTITY_UNVERIFIED_MESSAGE,
    BrightspaceIdentityProvider,
    parse_whoami,
)
from d2l_cli.providers.brightspace_quizzes import (
    BrightspaceQuizProvider,
    parse_attempts,
    parse_quizzes,
)
from d2l_cli.quiz_service import QuizService

BASE = "https://example.brightspace.com"
COURSE = "620000"
QUIZ = "4401"
VERSIONS = [{"ProductCode": "le", "SupportedVersions": ["1.74", "1.82", "1.90"]}]
LIST_PATH = f"/d2l/api/le/1.90/{COURSE}/quizzes/"
ATTEMPTS_PATH = f"/d2l/api/le/1.90/{COURSE}/quizzes/{QUIZ}/attempts/"


def raw_quiz(quiz_id: int = 4401, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "QuizId": quiz_id,
        "Name": f"Quiz {quiz_id}",
        "IsActive": True,
        "StartDate": "2026-03-01T14:00:00.000Z",
        "EndDate": "2026-03-20T04:59:00.000Z",
        "DueDate": "2026-03-15T04:59:00.000Z",
        "Description": {
            "Text": {"Text": "Chapter 3", "Html": "<p>Chapter 3</p>"},
            "IsDisplayed": True,
        },
        "SubmissionTimeLimit": {"IsEnforced": True, "ShowClock": True, "TimeLimitValue": 45},
        "AttemptsAllowed": {"IsUnlimited": False, "NumberOfAttemptsAllowed": 2},
    }
    body.update(extra)
    return body


def raw_attempt(attempt_id: int = 1, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "AttemptId": attempt_id,
        "QuizId": int(QUIZ),
        "UserId": 9001,
        "AttemptNumber": attempt_id,
        "Started": "2026-03-10T15:00:00.000Z",
        "Completed": "2026-03-10T15:30:00.000Z",
        "Score": 8.5,
        "IsPublished": True,
        "AttemptFeedback": {"Text": "SECRET FEEDBACK"},
    }
    body.update(extra)
    return body


def client() -> D2LHttpClient:
    return D2LHttpClient(BASE, auth_header_provider=lambda: {"Cookie": "session=fictional"})


# --- quiz metadata -------------------------------------------------------------------------------


def test_quiz_metadata_is_normalized_from_the_documented_shape() -> None:
    quiz = parse_quizzes([raw_quiz()], course_id=COURSE).quizzes[0]
    assert (quiz.quiz_id, quiz.name, quiz.active) == ("4401", "Quiz 4401", True)
    assert quiz.start_date == "2026-03-01T14:00:00.000Z" and quiz.due_date.endswith("Z")  # type: ignore[union-attr]
    assert (quiz.time_limit_minutes, quiz.time_limit_enforced) == (45, True)
    assert (quiz.attempts_allowed, quiz.attempts_unlimited) == (2, False)
    assert quiz.description == "Chapter 3"


def test_unreported_values_are_null_never_zero_or_guessed() -> None:
    node = {"QuizId": 5, "Name": "Bare"}
    quiz = parse_quizzes([node], course_id=COURSE).quizzes[0]
    assert quiz.start_date is None and quiz.due_date is None and quiz.end_date is None
    assert quiz.time_limit_minutes is None and quiz.time_limit_enforced is None
    assert quiz.attempts_allowed is None and quiz.attempts_unlimited is None
    assert quiz.active is None and quiz.description is None


def test_an_unenforced_time_limit_and_unlimited_attempts_are_not_reported_as_numbers() -> None:
    node = raw_quiz(
        SubmissionTimeLimit={"IsEnforced": False, "TimeLimitValue": 60},
        AttemptsAllowed={"IsUnlimited": True, "NumberOfAttemptsAllowed": 1},
    )
    quiz = parse_quizzes([node], course_id=COURSE).quizzes[0]
    assert quiz.time_limit_minutes is None and quiz.time_limit_enforced is False
    assert quiz.attempts_allowed is None and quiz.attempts_unlimited is True


def test_a_description_the_instructor_hides_is_not_shown() -> None:
    node = raw_quiz(Description={"Text": {"Text": "hidden"}, "IsDisplayed": False})
    assert parse_quizzes([node], course_id=COURSE).quizzes[0].description is None


def test_quiz_text_is_sanitized() -> None:
    node = raw_quiz(
        Name="Quiz\x1b[2J 1",
        Description={"Text": {"Html": "<script>x()</script><b>Hi</b>"}, "IsDisplayed": True},
    )
    quiz = parse_quizzes([node], course_id=COURSE).quizzes[0]
    assert "\x1b" not in quiz.name and quiz.description == "Hi"


@pytest.mark.parametrize(
    "payload",
    [
        "text",
        {"nope": []},
        [raw_quiz(), 3],
        [raw_quiz(QuizId="x")],
        [raw_quiz(), raw_quiz()],
        [raw_quiz(Name=None)],
        [raw_quiz(DueDate="tomorrow")],
        [raw_quiz(IsActive="yes")],
        [raw_quiz(SubmissionTimeLimit="45")],
        [raw_quiz(SubmissionTimeLimit={"IsEnforced": True, "TimeLimitValue": -1})],
        [raw_quiz(AttemptsAllowed={"IsUnlimited": False, "NumberOfAttemptsAllowed": True})],
    ],
)
def test_malformed_quiz_responses_fail_closed(payload: object) -> None:
    with pytest.raises(ProviderEndpointChangedError, match="Quiz structure is unsupported"):
        parse_quizzes(payload, course_id=COURSE)


def test_an_empty_valid_list_is_empty_and_complete_but_not_proof_of_no_quizzes() -> None:
    listing = parse_quizzes([], course_id=COURSE)
    assert listing.quizzes == () and listing.complete is True


def test_more_pages_mark_the_listing_incomplete() -> None:
    listing = parse_quizzes({"Objects": [raw_quiz()], "Next": "https://x/y"}, course_id=COURSE)
    assert listing.has_more and not listing.complete


# --- attempts: ownership and release semantics ---------------------------------------------------

ME = "9001"


def attempts_of(payload: object) -> AttemptListing:
    return parse_attempts(payload, course_id=COURSE, quiz_id=QUIZ, user_id=ME)


def test_feedback_publication_is_reported_but_never_as_a_released_score() -> None:
    published = attempts_of([raw_attempt(1)]).attempts[0]
    unpublished = attempts_of([raw_attempt(1, IsPublished=False)]).attempts[0]
    unknown = attempts_of([raw_attempt(1, IsPublished=None)]).attempts[0]
    assert (published.feedback_published, unpublished.feedback_published) == (True, False)
    assert unknown.feedback_published is None
    for attempt in (published, unpublished, unknown):
        assert not hasattr(attempt, "score") and not hasattr(attempt, "score_released")
        assert "8.5" not in repr(attempt)


@pytest.mark.parametrize("score", [None, 0, 0.0, 8.5, 100])
@pytest.mark.parametrize("published", [True, False, None])
def test_no_combination_of_score_and_publication_exposes_a_score(
    score: float | None, published: bool | None
) -> None:
    attempt = attempts_of([raw_attempt(1, Score=score, IsPublished=published)]).attempts[0]
    assert not hasattr(attempt, "score") and not hasattr(attempt, "score_released")


@pytest.mark.parametrize(
    ("started", "completed", "status", "done"),
    [
        ("2026-03-10T15:00:00Z", "2026-03-10T15:30:00Z", AttemptStatus.COMPLETED, True),
        ("2026-03-10T15:00:00Z", None, AttemptStatus.IN_PROGRESS, False),
        (None, None, AttemptStatus.UNKNOWN, False),
    ],
)
def test_attempt_status_reflects_only_what_the_record_shows(
    started: str | None, completed: str | None, status: AttemptStatus, done: bool
) -> None:
    node = raw_attempt(1, Started=started, Completed=completed, Score=5)
    attempt = attempts_of([node]).attempts[0]
    assert attempt.status is status and attempt.attempt_completed is done


def test_a_completed_attempt_is_not_a_published_grade() -> None:
    attempt = attempts_of([raw_attempt(1, IsPublished=False)]).attempts[0]
    assert attempt.attempt_completed is True and attempt.feedback_published is False


def test_feedback_and_user_ids_never_reach_the_model() -> None:
    attempt = attempts_of([raw_attempt(1)]).attempts[0]
    assert "SECRET" not in repr(attempt) and ME not in repr(attempt)
    assert not hasattr(attempt, "feedback") and not hasattr(attempt, "user_id")


@pytest.mark.parametrize(
    ("node", "code"),
    [
        (raw_attempt(1, UserId=9002), "ATTEMPT_OWNER_MISMATCH"),
        (raw_attempt(1, UserId=None), "ATTEMPT_OWNER_MISSING"),
        (raw_attempt(1, UserId="abc"), "ATTEMPT_OWNER"),
        (raw_attempt(1, UserId=True), "ATTEMPT_OWNER"),
        (raw_attempt(1, QuizId=999), "QUIZ_MISMATCH"),
    ],
)
def test_an_attempt_not_provably_the_signed_in_users_for_this_quiz_fails_closed(
    node: dict[str, Any], code: str
) -> None:
    node = dict(node)
    if code == "ATTEMPT_OWNER_MISSING":
        del node["UserId"]
    with pytest.raises(ProviderEndpointChangedError, match=code):
        attempts_of([node])


def test_a_single_other_user_response_is_refused_not_shown() -> None:
    # Only one distinct user id, so a "no conflicting ids" check would have accepted this.
    with pytest.raises(ProviderEndpointChangedError, match="ATTEMPT_OWNER_MISMATCH"):
        attempts_of([raw_attempt(1, UserId=9002), raw_attempt(2, UserId=9002)])


def test_a_mixed_user_response_is_refused_entirely() -> None:
    with pytest.raises(ProviderEndpointChangedError, match="ATTEMPT_OWNER_MISMATCH"):
        attempts_of([raw_attempt(1, UserId=9001), raw_attempt(2, UserId=9002)])


def test_several_attempts_by_the_signed_in_user_are_fine() -> None:
    assert [a.attempt_number for a in attempts_of([raw_attempt(1), raw_attempt(2)]).attempts] == [
        1,
        2,
    ]


@pytest.mark.parametrize(
    "payload",
    [
        [raw_attempt(1), raw_attempt(1)],
        [raw_attempt(1, Score="high")],
        [raw_attempt(1, Score=float("nan"))],
        [raw_attempt(1, Started="yesterday")],
        [raw_attempt(1, IsPublished="yes")],
        [raw_attempt(1, IsPublished=1)],
        [raw_attempt("x")],
        "nope",
    ],
)
def test_malformed_attempt_responses_fail_closed(payload: object) -> None:
    with pytest.raises(ProviderEndpointChangedError, match="Quiz structure is unsupported"):
        attempts_of(payload)


def test_an_empty_attempt_list_makes_no_claim_about_whether_the_quiz_was_attempted() -> None:
    listing = attempts_of([])
    assert listing.attempts == () and listing.complete
    assert not hasattr(listing, "attempted") and not hasattr(listing, "not_attempted")


# --- identity (whoami) ---------------------------------------------------------------------------

WHOAMI_PATH = "/d2l/api/lp/1.60/users/whoami"
LP = {"ProductCode": "lp", "SupportedVersions": ["1.40", "1.49", "1.60"]}
ALL_VERSIONS = [*VERSIONS, LP]


def mock_identity(identifier: object = 9001) -> respx.Route:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=ALL_VERSIONS))
    return respx.get(f"{BASE}{WHOAMI_PATH}").mock(
        return_value=httpx.Response(200, json={"Identifier": identifier, "FirstName": "A"})
    )


@pytest.mark.parametrize(
    ("body", "ok"),
    [
        ({"Identifier": 9001}, True),
        ({"Identifier": "9001"}, True),
        ({"Identifier": 0}, False),
        ({"Identifier": -5}, False),
        ({"Identifier": "0"}, False),
        ({"Identifier": "09001"}, False),
        ({"Identifier": True}, False),
        ({"Identifier": 9001.0}, False),
        ({"Identifier": "9001 "}, False),
        ({"Identifier": "9001/../1"}, False),
        ({"Identifier": ""}, False),
        ({"Identifier": None}, False),
        ({"Identifier": ["9001"]}, False),
        ({"UserId": 9001}, False),
        ({}, False),
        ([{"Identifier": 9001}], False),
        ("9001", False),
    ],
)
def test_whoami_must_yield_exactly_one_positive_numeric_id(body: object, ok: bool) -> None:
    if ok:
        assert parse_whoami(body) == "9001"
    else:
        with pytest.raises(ValueError):
            parse_whoami(body)


@respx.mock
def test_identity_uses_the_lp_route_with_its_own_version_floor_and_is_cached_per_instance() -> None:
    route = mock_identity()
    provider = BrightspaceIdentityProvider(client())
    assert provider.current_user_id() == "9001" and provider.current_user_id() == "9001"
    assert route.call_count == 1 and route.calls.last.request.method == "GET"
    assert route.calls.last.request.headers["cookie"] == "session=fictional"
    # A new provider (new command / new origin) never reuses another instance's identity.
    BrightspaceIdentityProvider(client()).current_user_id()
    assert route.call_count == 2


@respx.mock
def test_lp_versions_below_the_documented_floor_cannot_verify_identity() -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(
        return_value=httpx.Response(
            200,
            json=[{"ProductCode": "lp", "SupportedVersions": ["1.46", "1.48"]}, *VERSIONS],
        )
    )
    whoami = respx.get(url__regex=r".*/users/whoami").mock(
        return_value=httpx.Response(200, json={})
    )
    with pytest.raises(IdentityUnverifiedError):
        BrightspaceIdentityProvider(client()).current_user_id()
    assert whoami.call_count == 0


@respx.mock
@pytest.mark.parametrize("status", [403, 404, 500])
def test_whoami_denial_or_absence_is_an_unverified_identity(status: int) -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=ALL_VERSIONS))
    respx.get(f"{BASE}{WHOAMI_PATH}").mock(return_value=httpx.Response(status, text="SECRET BODY"))
    with pytest.raises(IdentityUnverifiedError) as raised:
        BrightspaceIdentityProvider(client()).current_user_id()
    assert "SECRET" not in str(raised.value) and "9001" not in str(raised.value)


@respx.mock
def test_an_expired_session_or_rate_limit_is_passed_through_not_masked() -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=ALL_VERSIONS))
    route = respx.get(f"{BASE}{WHOAMI_PATH}")
    route.mock(return_value=httpx.Response(401))
    with pytest.raises(NotAuthenticatedError):
        BrightspaceIdentityProvider(client()).current_user_id()
    route.mock(return_value=httpx.Response(429, headers={"retry-after": "5"}))
    with pytest.raises(RateLimitedError):
        BrightspaceIdentityProvider(client()).current_user_id()


@respx.mock
@pytest.mark.parametrize("body", ["not json", {"Identifier": None}, [], {"Identifier": "x"}])
def test_malformed_whoami_is_an_unverified_identity(body: object) -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=ALL_VERSIONS))
    reply = (
        httpx.Response(200, text=body) if isinstance(body, str) else httpx.Response(200, json=body)
    )
    respx.get(f"{BASE}{WHOAMI_PATH}").mock(return_value=reply)
    with pytest.raises(IdentityUnverifiedError):
        BrightspaceIdentityProvider(client()).current_user_id()


# --- transport -----------------------------------------------------------------------------------


@respx.mock
def test_attempts_are_requested_only_after_identity_and_filtered_to_that_user() -> None:
    whoami = mock_identity()
    quizzes = respx.get(f"{BASE}{LIST_PATH}").mock(
        return_value=httpx.Response(200, json={"Objects": [raw_quiz()], "Next": None})
    )
    attempts = respx.get(f"{BASE}{ATTEMPTS_PATH}").mock(
        return_value=httpx.Response(200, json={"Objects": [raw_attempt()], "Next": None})
    )
    questions = respx.get(url__regex=r".*/questions.*").mock(
        return_value=httpx.Response(200, json=[])
    )
    other = respx.route(method__in=["POST", "PUT", "PATCH", "DELETE"]).mock(
        return_value=httpx.Response(200)
    )
    provider = BrightspaceQuizProvider(client())
    assert len(provider.list_quizzes(COURSE).quizzes) == 1
    assert whoami.call_count == 0  # listing quizzes needs no identity
    assert len(provider.list_attempts(COURSE, QUIZ).attempts) == 1
    assert quizzes.call_count == 1 and attempts.call_count == 1 and whoami.call_count == 1
    assert attempts.calls.last.request.url.params["userId"] == "9001"
    assert questions.call_count == 0 and other.call_count == 0


@respx.mock
def test_when_identity_cannot_be_verified_no_attempts_request_is_ever_made() -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=ALL_VERSIONS))
    respx.get(f"{BASE}{WHOAMI_PATH}").mock(return_value=httpx.Response(403))
    attempts = respx.get(url__regex=r".*/attempts.*").mock(
        return_value=httpx.Response(200, json=[raw_attempt()])
    )
    with pytest.raises(IdentityUnverifiedError):
        BrightspaceQuizProvider(client()).list_attempts(COURSE, QUIZ)
    assert attempts.call_count == 0  # and there is no unfiltered fallback


@respx.mock
def test_a_server_that_ignores_the_filter_and_returns_others_is_refused() -> None:
    mock_identity()
    respx.get(f"{BASE}{ATTEMPTS_PATH}").mock(
        return_value=httpx.Response(200, json=[raw_attempt(1, UserId=9002)])
    )
    with pytest.raises(ProviderEndpointChangedError, match="ATTEMPT_OWNER_MISMATCH"):
        BrightspaceQuizProvider(client()).list_attempts(COURSE, QUIZ)


@respx.mock
def test_a_permission_denial_on_attempts_is_an_error_not_an_empty_result() -> None:
    mock_identity()
    respx.get(f"{BASE}{ATTEMPTS_PATH}").mock(return_value=httpx.Response(403))
    with pytest.raises(PermissionDeniedError):
        BrightspaceQuizProvider(client()).list_attempts(COURSE, QUIZ)


@respx.mock
def test_unsupported_endpoint_and_version_failures_are_distinct_errors() -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=VERSIONS))
    respx.get(f"{BASE}{LIST_PATH}").mock(return_value=httpx.Response(404))
    from d2l_cli.client.errors import UnexpectedProviderError

    with pytest.raises(UnexpectedProviderError, match="404"):
        BrightspaceQuizProvider(client()).list_quizzes(COURSE)
    respx.get(f"{BASE}/d2l/api/versions/").mock(
        return_value=httpx.Response(
            200, json=[{"ProductCode": "le", "SupportedVersions": ["1.74"]}]
        )
    )
    with pytest.raises(ProviderEndpointChangedError, match="VERSION_UNSUPPORTED"):
        BrightspaceQuizProvider(client()).list_quizzes(COURSE)


@respx.mock
@pytest.mark.parametrize("bad", ["abc", "1/../2", "", "9" * 25])
def test_invalid_ids_are_rejected_before_any_request(bad: str) -> None:
    route = respx.route().mock(return_value=httpx.Response(200, json=[]))
    provider = BrightspaceQuizProvider(client())
    with pytest.raises(ValueError, match="numeric"):
        provider.list_quizzes(bad)
    with pytest.raises(ValueError, match="numeric"):
        provider.list_attempts(COURSE, bad)
    with pytest.raises(ValueError, match="numeric"):
        provider.list_attempts(bad, QUIZ)
    assert route.call_count == 0  # not even the identity request


# --- CLI -----------------------------------------------------------------------------------------

runner = CliRunner()


class _Auth:
    def __init__(self, headers: Mapping[str, str] | Exception) -> None:
        self._headers = headers

    def header_provider(self) -> Mapping[str, str]:
        if isinstance(self._headers, Exception):
            raise self._headers
        return self._headers


class _Service:
    def __init__(
        self, quizzes: QuizListing | Exception, attempts: AttemptListing | Exception
    ) -> None:
        self._quizzes, self._attempts = quizzes, attempts

    def list_quizzes(self, course_id: str) -> QuizListing:
        if isinstance(self._quizzes, Exception):
            raise self._quizzes
        return self._quizzes

    def list_attempts(self, course_id: str, quiz_id: str) -> AttemptListing:
        if isinstance(self._attempts, Exception):
            raise self._attempts
        return self._attempts


def wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    quizzes: Any = None,
    attempts: Any = None,
    auth: object | None = None,
) -> None:
    service = _Service(
        quizzes if quizzes is not None else parse_quizzes([raw_quiz()], course_id=COURSE),
        attempts if attempts is not None else attempts_of([raw_attempt()]),
    )
    monkeypatch.setattr(cli_module, "load_config", lambda: object())
    monkeypatch.setattr(
        cli_module, "_build_auth_service", lambda config: auth or _Auth({"Cookie": "x"})
    )
    monkeypatch.setattr(cli_module, "_build_quiz_service", lambda config, headers: service)


def test_quizzes_list_json_has_the_documented_stable_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    wire(monkeypatch)
    result = runner.invoke(cli_module.app, ["quizzes", "list", "--course", COURSE, "--json"])
    assert result.exit_code == 0 and result.stderr == ""
    body = json.loads(result.stdout)["quizzes"]
    assert list(body) == ["course_id", "complete", "quizzes"]
    assert list(body["quizzes"][0]) == [
        "quiz_id",
        "course_id",
        "name",
        "description",
        "active",
        "start_date",
        "end_date",
        "due_date",
        "time_limit_minutes",
        "time_limit_enforced",
        "attempts_allowed",
        "attempts_unlimited",
    ]


def test_quizzes_attempts_json_shape_and_conservative_release_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(monkeypatch)
    result = runner.invoke(
        cli_module.app, ["quizzes", "attempts", "--course", COURSE, "--quiz", QUIZ, "--json"]
    )
    assert result.exit_code == 0
    body = json.loads(result.stdout)["quiz_attempts"]
    assert list(body) == ["course_id", "quiz_id", "complete", "attempts_returned", "attempts"]
    attempt = body["attempts"][0]
    assert list(attempt) == [
        "attempt_id",
        "quiz_id",
        "attempt_number",
        "started",
        "completed",
        "status",
        "attempt_completed",
        "feedback_published",
        "score_released",
        "score",
    ]
    assert attempt["score"] is None and attempt["score_released"] is None  # never exposed
    assert attempt["feedback_published"] is True and attempt["attempt_completed"] is True
    for forbidden in ("SECRET", ME, "8.5"):
        assert forbidden not in result.stdout


def test_an_empty_quiz_list_warns_that_it_does_not_prove_there_are_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(monkeypatch, quizzes=parse_quizzes([], course_id=COURSE))
    result = runner.invoke(cli_module.app, ["quizzes", "list", "--course", COURSE, "--json"])
    assert result.exit_code == 0 and json.loads(result.stdout)["quizzes"]["quizzes"] == []
    assert "does not prove" in result.stderr


def test_an_empty_attempt_list_warns_it_does_not_prove_the_quiz_was_not_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(monkeypatch, attempts=attempts_of([]))
    result = runner.invoke(
        cli_module.app, ["quizzes", "attempts", "--course", COURSE, "--quiz", QUIZ]
    )
    assert result.exit_code == 0 and "does not prove" in result.stderr


def test_text_output_never_shows_a_score_and_shows_unlimited_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(
        monkeypatch,
        quizzes=parse_quizzes([raw_quiz(AttemptsAllowed={"IsUnlimited": True})], course_id=COURSE),
        attempts=attempts_of([raw_attempt(IsPublished=None)]),
    )
    listed = runner.invoke(cli_module.app, ["quizzes", "list", "--course", COURSE])
    assert "attempts unlimited" in listed.stdout and "limit 45m" in listed.stdout
    attempts = runner.invoke(
        cli_module.app, ["quizzes", "attempts", "--course", COURSE, "--quiz", QUIZ]
    )
    assert "score not shown" in attempts.stdout and "feedback published unknown" in attempts.stdout
    assert "8.5" not in attempts.stdout


@pytest.mark.parametrize(
    "error",
    [
        PermissionDeniedError("provider denied access to this resource"),
        ProviderEndpointChangedError("Quiz structure is unsupported (ATTEMPT_OWNER_MISMATCH)"),
        IdentityUnverifiedError(IDENTITY_UNVERIFIED_MESSAGE),
    ],
)
def test_errors_exit_one_with_empty_stdout(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    wire(monkeypatch, quizzes=error, attempts=error)
    for args in (["list"], ["attempts", "--quiz", QUIZ]):
        result = runner.invoke(cli_module.app, ["quizzes", *args, "--course", COURSE, "--json"])
        assert result.exit_code == 1 and result.stdout == "" and "Cookie" not in result.stderr
        assert str(error) in result.stderr


def test_authentication_failure_and_missing_quiz_option(monkeypatch: pytest.MonkeyPatch) -> None:
    wire(
        monkeypatch,
        auth=_Auth(NotAuthenticatedError("No stored session; run 'd2l auth login'.")),
    )
    result = runner.invoke(cli_module.app, ["quizzes", "list", "--course", COURSE, "--json"])
    assert result.exit_code == 1 and result.stdout == "" and "auth login" in result.stderr
    usage = runner.invoke(cli_module.app, ["quizzes", "attempts", "--course", COURSE, "--json"])
    assert usage.exit_code == 2 and usage.stdout == ""


def test_the_service_rejects_a_response_for_another_course() -> None:
    class Mismatched(BrightspaceQuizProvider):
        def __init__(self) -> None:
            pass

        def list_quizzes(self, course_id: str) -> QuizListing:
            return parse_quizzes([raw_quiz()], course_id="999")

    with pytest.raises(ProviderEndpointChangedError, match="mismatched"):
        QuizService(Mismatched()).list_quizzes(COURSE)
