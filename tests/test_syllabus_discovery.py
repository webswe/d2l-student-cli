"""Syllabus discovery: bounded binary GET, fail-closed content parsing, ranking, fetch, CLI.

All data is fictional. The mocked transport stands in for Brightspace: the real tenant is only ever
contacted by the owner's own authenticated session (see docs/SYLLABUS_DISCOVERY.md).
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from itertools import count
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

import d2l_cli.cli as cli_module
from d2l_cli.client.errors import (
    MalformedResponseError,
    NotAuthenticatedError,
    ProviderEndpointChangedError,
    UnsafeRequestTargetError,
)
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.models.syllabus import ContentTopic, SyllabusDocument, SyllabusSearch, TopicKind
from d2l_cli.providers.brightspace_content import (
    MAX_DOCUMENT_BYTES,
    BrightspaceContentProvider,
    parse_toc,
    select_version,
)
from d2l_cli.syllabus_service import SyllabusService, rank_candidates, recommend, score_topic

BASE = "https://example.brightspace.com"
COURSE = "620000"
PDF = b"%PDF-1.7\n% fictional syllabus\n"
VERSIONS = [
    {"ProductCode": "le", "LatestVersion": "1.80", "SupportedVersions": ["1.40", "1.74", "1.80"]}
]
_MODULE_IDS = count(1)


def topic(
    tid: int, title: str, kind: str = "File", url: str | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "TopicId": tid,
        "Title": title,
        "TypeIdentifier": kind,
        "Url": url if url is not None else f"/content/enforced/{COURSE}-OU/{title}.pdf",
        **extra,
    }


def toc(*modules: dict[str, Any]) -> dict[str, Any]:
    return {"Modules": list(modules)}


def module(
    title: str,
    topics: list[dict[str, Any]] | None = None,
    modules: list[Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "ModuleId": next(_MODULE_IDS),
        "Title": title,
        "Topics": topics or [],
        "Modules": modules or [],
        **extra,
    }


def flat(topics: tuple[ContentTopic, ...]) -> dict[str, ContentTopic]:
    return {t.title: t for t in topics}


# --- transport: bounded binary GET --------------------------------------------------------------


def client() -> D2LHttpClient:
    return D2LHttpClient(BASE, auth_header_provider=lambda: {"Cookie": "session=fictional"})


@respx.mock
def test_get_bytes_returns_bounded_content_and_declared_type() -> None:
    route = respx.get(f"{BASE}/d2l/x").mock(
        return_value=httpx.Response(
            200, content=PDF, headers={"content-type": "Application/PDF; charset=x"}
        )
    )
    response = client().get_bytes(
        "/d2l/x", allowed_media_types=frozenset({"application/pdf"}), max_bytes=1000
    )
    assert response.content == PDF and response.media_type == "application/pdf"
    assert route.calls.last.request.headers["accept-encoding"] == "identity"
    assert route.calls.last.request.method == "GET"


@respx.mock
def test_get_bytes_refuses_a_declared_oversize_body() -> None:
    respx.get(f"{BASE}/d2l/x").mock(
        return_value=httpx.Response(
            200,
            content=b"x" * 50,
            headers={"content-type": "application/pdf", "content-length": "5000"},
        )
    )
    with pytest.raises(MalformedResponseError, match="safe size limit"):
        client().get_bytes(
            "/d2l/x", allowed_media_types=frozenset({"application/pdf"}), max_bytes=100
        )


@respx.mock
def test_get_bytes_refuses_a_streamed_oversize_body_without_a_length_header() -> None:
    respx.get(f"{BASE}/d2l/x").mock(
        return_value=httpx.Response(
            200, content=iter([b"x" * 60, b"x" * 60]), headers={"content-type": "application/pdf"}
        )
    )
    with pytest.raises(MalformedResponseError, match="safe size limit"):
        client().get_bytes(
            "/d2l/x", allowed_media_types=frozenset({"application/pdf"}), max_bytes=100
        )


@respx.mock
def test_get_bytes_refuses_a_type_outside_the_allowlist() -> None:
    respx.get(f"{BASE}/d2l/x").mock(
        return_value=httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
    )
    with pytest.raises(MalformedResponseError, match="media type"):
        client().get_bytes(
            "/d2l/x", allowed_media_types=frozenset({"application/pdf"}), max_bytes=100
        )


@respx.mock
def test_get_bytes_does_not_follow_a_redirect_and_names_an_expired_session() -> None:
    respx.get(f"{BASE}/d2l/x").mock(
        return_value=httpx.Response(302, headers={"location": f"{BASE}/d2l/login"})
    )
    with pytest.raises(NotAuthenticatedError):
        client().get_bytes(
            "/d2l/x", allowed_media_types=frozenset({"application/pdf"}), max_bytes=100
        )


@pytest.mark.parametrize(
    "target", ["https://evil.example/x", "//evil.example/x", "d2l/x", "/d2l/x#f", "/d2l/\\x"]
)
def test_get_bytes_rejects_anything_but_a_relative_path_before_any_io(target: str) -> None:
    with pytest.raises(UnsafeRequestTargetError):
        client().get_bytes(
            target, allowed_media_types=frozenset({"application/pdf"}), max_bytes=100
        )


@pytest.mark.parametrize("limit", [0, -1, 10**9])
def test_get_bytes_rejects_an_unreasonable_limit(limit: int) -> None:
    with pytest.raises(ValueError):
        client().get_bytes(
            "/d2l/x", allowed_media_types=frozenset({"application/pdf"}), max_bytes=limit
        )


# --- API version -------------------------------------------------------------------------------


def test_select_version_picks_the_newest_inside_the_supported_range() -> None:
    assert select_version(VERSIONS) == "1.74"  # 1.80 is above the range this code was written for


@pytest.mark.parametrize(
    "payload",
    [
        {"not": "a list"},
        [{"ProductCode": "lp", "SupportedVersions": ["1.47"]}],
        [{"ProductCode": "le", "SupportedVersions": ["1.5", "2.0", "1.200"]}],
        [{"ProductCode": "le", "SupportedVersions": "1.74"}],
        [],
    ],
)
def test_select_version_fails_closed(payload: object) -> None:
    with pytest.raises(ProviderEndpointChangedError):
        select_version(payload)


# --- table of contents -------------------------------------------------------------------------


def test_parse_toc_flattens_modules_with_their_trail_and_kinds() -> None:
    payload = toc(
        module(
            "Start Here",
            [
                topic(11, "Course Syllabus"),
                topic(12, "Welcome video", kind="Link", url="https://youtu.be/x"),
            ],
            modules=[
                module("Policies", [topic(13, "Late Policy", url="/content/enforced/1/late.docx")])
            ],
        )
    )
    by_title = flat(parse_toc(payload, course_id=COURSE))
    assert by_title["Course Syllabus"].module_path == ("Start Here",)
    assert (
        by_title["Course Syllabus"].kind is TopicKind.FILE
        and by_title["Course Syllabus"].file_extension == "pdf"
    )
    assert (
        by_title["Welcome video"].kind is TopicKind.OTHER
        and by_title["Welcome video"].file_extension is None
    )
    assert by_title["Late Policy"].module_path == ("Start Here", "Policies")
    assert by_title["Late Policy"].file_extension == "docx"


def test_parse_toc_drops_what_a_student_cannot_see() -> None:
    payload = toc(
        module("Hidden module", [topic(1, "Syllabus")], IsHidden=True),
        module(
            "Shown",
            [
                topic(2, "Syllabus hidden", IsHidden=True),
                topic(3, "Syllabus broken", IsBroken=True),
                topic(4, "Syllabus"),
            ],
        ),
    )
    assert [t.topic_id for t in parse_toc(payload, course_id=COURSE)] == ["4"]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"Modules": "nope"},
        {"Modules": ["not a dict"]},
        toc(module("M", [{"TopicId": "7", "Title": "x", "TypeIdentifier": "File"}])),
        toc(module("M", [{"TopicId": True, "Title": "x", "TypeIdentifier": "File"}])),
        toc(module("M", [{"TopicId": 7, "Title": 5, "TypeIdentifier": "File"}])),
        toc(module("M", [{"TopicId": 7, "Title": "x"}])),
        toc(module("M", [topic(7, "x", IsHidden="yes")])),
        toc(module("M", [topic(7, "x"), topic(7, "y")])),
        toc({"ModuleId": 1, "Title": "M", "Topics": "oops"}),
    ],
)
def test_parse_toc_fails_closed_on_any_deviation(payload: object) -> None:
    with pytest.raises(ProviderEndpointChangedError):
        parse_toc(payload, course_id=COURSE)


def test_parse_toc_bounds_depth_and_size() -> None:
    deep: dict[str, Any] = module("leaf")
    for _ in range(12):
        deep = module("wrap", modules=[deep])
    with pytest.raises(ProviderEndpointChangedError):
        parse_toc(toc(deep), course_id=COURSE)
    wide = toc(module("M", [topic(i, f"t{i}") for i in range(10_001)]))
    with pytest.raises(ProviderEndpointChangedError):
        parse_toc(wide, course_id=COURSE)


def test_parse_toc_sanitizes_provider_text_and_never_requests_the_advertised_url() -> None:
    payload = toc(
        module("M\x1b[31m", [topic(5, "Syl\x00labus\n", url="https://evil.example/x.pdf")])
    )
    parsed = parse_toc(payload, course_id=COURSE)[0]
    assert parsed.title == "Syllabus" and parsed.module_path == ("M",)


def test_parse_toc_rejects_a_non_numeric_course_id() -> None:
    with pytest.raises(ValueError):
        parse_toc(toc(), course_id="../1")


# --- ranking and recommendation ----------------------------------------------------------------


def make(
    title: str,
    *,
    path: tuple[str, ...] = ("Start",),
    kind: TopicKind = TopicKind.FILE,
    ext: str | None = "pdf",
    tid: str = "1",
) -> ContentTopic:
    return ContentTopic(COURSE, tid, title, path, kind, ext, None)


def test_a_title_naming_the_syllabus_beats_a_module_that_only_contains_it() -> None:
    titled = score_topic(make("CSE 2300 Syllabus"))
    in_module = score_topic(make("CSE 2300 Fall 2026", path=("Syllabus",)))
    assert titled is not None and in_module is not None and titled.score > in_module.score
    assert (
        titled.reasons[0] == "title_mentions_syllabus"
        and "module_mentions_syllabus" in in_module.reasons
    )


def test_unrelated_topics_are_not_candidates() -> None:
    assert score_topic(make("Week 3 slides")) is None
    assert score_topic(make("Textbook list", path=("Resources",))) is None


def test_an_acknowledgement_quiz_is_never_recommended() -> None:
    quiz = score_topic(make("Syllabus Quiz", kind=TopicKind.OTHER, ext=None))
    ack = score_topic(make("Syllabus Acknowledgement Form"))
    assert quiz is not None and not quiz.fetchable and "not_a_file" in quiz.reasons
    assert ack is not None and "looks_like_an_acknowledgement" in ack.reasons
    assert (
        recommend(
            rank_candidates(
                (
                    make("Syllabus Quiz", kind=TopicKind.OTHER, ext=None, tid="1"),
                    make("Syllabus Acknowledgement Form", tid="2"),
                )
            )
        )
        is None
    )


def test_an_unsupported_file_type_is_listed_but_not_fetchable() -> None:
    for extension in ("xlsx", "pptx", "doc", "zip"):  # only PDF, plain text and .docx are read
        other = score_topic(make("Syllabus", ext=extension))
        assert other is not None and not other.fetchable
        assert "unsupported_file_type" in other.reasons
    word = score_topic(make("Syllabus", ext="docx"))
    assert word is not None and word.fetchable and "downloadable_document" in word.reasons


def test_one_clear_leader_is_recommended() -> None:
    topics = (
        make("Course Syllabus", tid="1"),
        make("Syllabus Acknowledgement", tid="2"),
        make("Week 1", tid="3"),
    )
    assert recommend(rank_candidates(topics)) == "1"


def test_two_equally_strong_syllabus_files_are_left_to_the_person() -> None:
    topics = (make("Syllabus (Section A)", tid="1"), make("Syllabus (Section B)", tid="2"))
    assert recommend(rank_candidates(topics)) is None
    assert [c.topic.topic_id for c in rank_candidates(topics)] == [
        "1",
        "2",
    ]  # ordered, deterministic


def test_an_outline_alone_is_never_recommended() -> None:
    outline = rank_candidates((make("Course Outline", tid="1"),))
    assert [c.reasons[0] for c in outline] == ["title_looks_like_an_outline"]
    assert recommend(outline) is None


# --- provider + service end to end over the mocked transport -----------------------------------


def service() -> SyllabusService:
    return SyllabusService(BrightspaceContentProvider(client()))


def mock_course(extra_topics: list[dict[str, Any]] | None = None) -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=VERSIONS))
    topics = [
        topic(11, "Course Syllabus"),
        topic(12, "Syllabus Quiz", kind="Quiz"),
        *(extra_topics or []),
    ]
    respx.get(f"{BASE}/d2l/api/le/1.74/{COURSE}/content/toc").mock(
        return_value=httpx.Response(200, json=toc(module("Start Here", topics)))
    )


@respx.mock
def test_find_returns_ranked_candidates_and_the_recommendation() -> None:
    mock_course()
    search = service().find(COURSE)
    assert search.api_version == "1.74" and search.topic_count == 2
    assert search.recommended_topic_id == "11"
    assert [c.topic.title for c in search.candidates][0] == "Course Syllabus"


@respx.mock
def test_fetch_downloads_only_through_the_documented_file_route() -> None:
    mock_course()
    file_route = respx.get(f"{BASE}/d2l/api/le/1.74/{COURSE}/content/topics/11/file").mock(
        return_value=httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
    )
    doc = service().fetch(COURSE, "11")
    assert doc.content == PDF and doc.sha256 == hashlib.sha256(PDF).hexdigest()
    assert doc.media_type == "application/pdf" and doc.file_name == "Course Syllabus.pdf"
    assert file_route.call_count == 1
    assert all(call.request.url.host == "example.brightspace.com" for call in respx.calls)
    assert all(call.request.method == "GET" for call in respx.calls)
    assert not any(
        "youtu" in str(call.request.url) or ".pdf" in call.request.url.path for call in respx.calls
    )


@respx.mock
def test_fetch_refuses_a_topic_the_course_does_not_list_without_requesting_a_file() -> None:
    mock_course()
    file_route = respx.get(url__regex=r".*/content/topics/\d+/file").mock(
        return_value=httpx.Response(200, content=PDF)
    )
    for bad in ("999", "12"):  # not listed at all / listed but not a file
        with pytest.raises(ValueError, match="not a downloadable document"):
            service().fetch(COURSE, bad)
    assert file_route.call_count == 0


@respx.mock
def test_fetch_checks_the_bytes_against_the_declared_type() -> None:
    mock_course()
    respx.get(f"{BASE}/d2l/api/le/1.74/{COURSE}/content/topics/11/file").mock(
        return_value=httpx.Response(
            200, content=b"<html>not a pdf</html>", headers={"content-type": "application/pdf"}
        )
    )
    with pytest.raises(MalformedResponseError, match="did not match"):
        service().fetch(COURSE, "11")


@respx.mock
def test_fetch_accepts_plain_text_and_refuses_binary_that_is_not_a_pdf() -> None:
    mock_course()
    route = respx.get(f"{BASE}/d2l/api/le/1.74/{COURSE}/content/topics/11/file")
    route.mock(
        return_value=httpx.Response(
            200,
            content=b"Grading\nHomework 40%\n",
            headers={"content-type": "text/plain; charset=utf-8"},
        )
    )
    assert service().fetch(COURSE, "11").file_name == "Course Syllabus.txt"
    route.mock(
        return_value=httpx.Response(
            200,
            content=b"PK\x03\x04\x00\x00zip",
            headers={"content-type": "application/octet-stream"},
        )
    )
    with pytest.raises(MalformedResponseError, match="not a safe Word document"):
        service().fetch(COURSE, "11")


@respx.mock
def test_fetch_refuses_an_empty_or_oversize_document() -> None:
    mock_course()
    route = respx.get(f"{BASE}/d2l/api/le/1.74/{COURSE}/content/topics/11/file")
    route.mock(
        return_value=httpx.Response(200, content=b"", headers={"content-type": "application/pdf"})
    )
    with pytest.raises(MalformedResponseError, match="empty"):
        service().fetch(COURSE, "11")
    route.mock(
        return_value=httpx.Response(
            200,
            content=b"x",
            headers={
                "content-type": "application/pdf",
                "content-length": str(MAX_DOCUMENT_BYTES + 1),
            },
        )
    )
    with pytest.raises(MalformedResponseError, match="safe size limit"):
        service().fetch(COURSE, "11")


@respx.mock
def test_the_file_name_never_comes_from_the_provider_or_escapes_a_directory() -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=VERSIONS))
    respx.get(f"{BASE}/d2l/api/le/1.74/{COURSE}/content/toc").mock(
        return_value=httpx.Response(
            200, json=toc(module("S", [topic(11, "..\\..\\Windows/System32/Syllabus")]))
        )
    )
    respx.get(f"{BASE}/d2l/api/le/1.74/{COURSE}/content/topics/11/file").mock(
        return_value=httpx.Response(
            200,
            content=PDF,
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="../../evil.exe"',
            },
        )
    )
    name = service().fetch(COURSE, "11").file_name
    assert (
        name == "WindowsSystem32Syllabus.pdf"
        and "/" not in name
        and "\\" not in name
        and ".." not in name
    )


def test_ids_must_be_numeric_before_any_request() -> None:
    provider = BrightspaceContentProvider(client())
    with pytest.raises(ValueError):
        provider.list_topics("1/../2")
    with pytest.raises(ValueError):
        provider.download_topic_file(COURSE, "1?x=y")


# --- command line ------------------------------------------------------------------------------

runner = CliRunner()


class _Auth:
    def __init__(self, headers: Mapping[str, str] | Exception) -> None:
        self._headers = headers

    def header_provider(self) -> Mapping[str, str]:
        if isinstance(self._headers, Exception):
            raise self._headers
        return self._headers


class _Service:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def find(self, course_id: str) -> SyllabusSearch:
        if self._error:
            raise self._error
        return SyllabusService(_FixedProvider()).find(course_id)

    def fetch(self, course_id: str, topic_id: str) -> SyllabusDocument:
        if self._error:
            raise self._error
        return SyllabusDocument(
            course_id,
            topic_id,
            "Course Syllabus",
            ("Start",),
            "Course Syllabus.pdf",
            "application/pdf",
            PDF,
            hashlib.sha256(PDF).hexdigest(),
            "1.74",
        )


class _FixedProvider(BrightspaceContentProvider):
    def __init__(self) -> None:  # no client: only the two reads below are used
        pass

    def api_version(self) -> str:
        return "1.74"

    def file_api_version(self) -> str:
        return "1.74"

    def list_topics(self, course_id: str) -> tuple[ContentTopic, ...]:
        return (make("Course Syllabus", tid="11"),)


def wire(
    monkeypatch: pytest.MonkeyPatch, *, service: object | None = None, auth: object | None = None
) -> None:
    monkeypatch.setattr(cli_module, "load_config", lambda: object())
    monkeypatch.setattr(
        cli_module, "_build_auth_service", lambda config: auth or _Auth({"Cookie": "x"})
    )
    monkeypatch.setattr(
        cli_module, "_build_syllabus_service", lambda config, headers: service or _Service()
    )


def test_cli_find_json_has_the_documented_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    wire(monkeypatch)
    result = runner.invoke(cli_module.app, ["syllabus", "find", "--course", COURSE, "--json"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)["syllabus_search"]
    assert body["course_id"] == COURSE and body["recommended_topic_id"] == "11"
    assert set(body["candidates"][0]) == {
        "topic_id",
        "title",
        "module_path",
        "kind",
        "file_extension",
        "last_modified",
        "score",
        "reasons",
        "fetchable",
    }


def test_cli_fetch_json_carries_the_document_as_base64_and_a_checksum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(monkeypatch)
    result = runner.invoke(
        cli_module.app, ["syllabus", "fetch", "--course", COURSE, "--topic", "11", "--json"]
    )
    assert result.exit_code == 0
    doc = json.loads(result.stdout)["syllabus_document"]
    assert base64.b64decode(doc["content_base64"]) == PDF
    assert doc["sha256"] == hashlib.sha256(PDF).hexdigest() and doc["size_bytes"] == len(PDF)
    assert doc["module_path"] == ["Start"]
    assert set(doc) == {
        "course_id",
        "topic_id",
        "title",
        "module_path",
        "file_name",
        "media_type",
        "size_bytes",
        "sha256",
        "api_version",
        "content_base64",
    }


def test_cli_fetch_without_json_is_a_usage_error_and_prints_no_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(monkeypatch)
    result = runner.invoke(
        cli_module.app, ["syllabus", "fetch", "--course", COURSE, "--topic", "11"]
    )
    assert result.exit_code == 2 and "JFVBR" not in result.stdout and result.stdout == ""


@pytest.mark.parametrize("command", [["find"], ["fetch", "--topic", "11"]])
def test_cli_failures_exit_one_with_empty_stdout_and_a_safe_message(
    monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    wire(
        monkeypatch,
        service=_Service(
            ProviderEndpointChangedError(
                "Course content structure is unsupported (CONTENT_TOC_SHAPE)"
            )
        ),
    )
    result = runner.invoke(cli_module.app, ["syllabus", *command, "--course", COURSE, "--json"])
    assert result.exit_code == 1 and result.stdout == ""
    assert "CONTENT_TOC_SHAPE" in result.stderr and "Cookie" not in result.stderr


def test_cli_authentication_failure_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    wire(monkeypatch, auth=_Auth(NotAuthenticatedError("No stored session; run 'd2l auth login'.")))
    result = runner.invoke(cli_module.app, ["syllabus", "find", "--course", COURSE, "--json"])
    assert result.exit_code == 1 and result.stdout == "" and "auth login" in result.stderr


def test_cli_find_text_marks_the_recommended_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    wire(monkeypatch)
    result = runner.invoke(cli_module.app, ["syllabus", "find", "--course", COURSE])
    assert result.exit_code == 0 and result.stdout.startswith("* 11")
