"""Modern file-route compatibility using fictional TOCs and an offline mocked transport."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

import d2l_cli.cli as cli_module
from d2l_cli.client.errors import (
    MalformedResponseError,
    NotAuthenticatedError,
    PermissionDeniedError,
    ProviderEndpointChangedError,
    RateLimitedError,
    UnexpectedProviderError,
)
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.docx_guard import DOCX_MEDIA_TYPE
from d2l_cli.providers.brightspace_content import MAX_DOCUMENT_BYTES, BrightspaceContentProvider
from d2l_cli.syllabus_service import SyllabusService
from tests.test_docx_syllabus import docx
from tests.test_syllabus_discovery import BASE, COURSE, PDF, client, module, toc, topic

FILE_PATH = f"{BASE}/d2l/api/le/1.82/{COURSE}/content/topics/11/file"


def mock_modern_course(*, item: dict[str, Any] | None = None) -> respx.Route:
    respx.get(f"{BASE}/d2l/api/versions/").mock(
        return_value=httpx.Response(
            200, json=[{"ProductCode": "le", "SupportedVersions": ["1.82"]}]
        )
    )
    respx.get(f"{BASE}/d2l/api/le/1.82/{COURSE}/content/toc").mock(
        return_value=httpx.Response(
            200, json=toc(module("Start", [item if item is not None else topic(11, "Syllabus")]))
        )
    )
    return respx.get(FILE_PATH)


def syllabus() -> SyllabusService:
    return SyllabusService(BrightspaceContentProvider(client()))


@respx.mock
@pytest.mark.parametrize(
    ("body", "declared", "expected_type"),
    [
        (PDF, "application/pdf", "application/pdf"),
        (b"Fictional syllabus\n", "text/plain; charset=utf-8", "text/plain"),
        (docx(), DOCX_MEDIA_TYPE, DOCX_MEDIA_TYPE),
    ],
)
def test_modern_file_route_accepts_only_valid_documents(
    body: bytes, declared: str, expected_type: str
) -> None:
    route = mock_modern_course().mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": declared})
    )
    document = syllabus().fetch(COURSE, "11")
    assert document.api_version == "1.82" and document.media_type == expected_type
    assert document.content == body and route.call_count == 1
    assert len(respx.calls) == 3
    assert all(call.request.method == "GET" for call in respx.calls)
    assert all(call.request.url.host == "example.brightspace.com" for call in respx.calls)


@respx.mock
def test_modern_find_and_fetch_keep_the_json_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_modern_course().mock(
        return_value=httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
    )

    class Auth:
        def header_provider(self) -> dict[str, str]:
            return {"Cookie": "fictional-session"}

    monkeypatch.setattr(cli_module, "load_config", lambda: object())
    monkeypatch.setattr(cli_module, "_build_auth_service", lambda config: Auth())
    monkeypatch.setattr(cli_module, "_build_syllabus_service", lambda config, headers: syllabus())
    runner = CliRunner()
    found = runner.invoke(cli_module.app, ["syllabus", "find", "--course", COURSE, "--json"])
    assert found.exit_code == 0
    search = json.loads(found.stdout)["syllabus_search"]
    assert search["api_version"] == "1.82" and search["recommended_topic_id"] == "11"
    fetched = runner.invoke(
        cli_module.app, ["syllabus", "fetch", "--course", COURSE, "--topic", "11", "--json"]
    )
    assert fetched.exit_code == 0
    document = json.loads(fetched.stdout)["syllabus_document"]
    assert document["api_version"] == "1.82"
    assert base64.b64decode(document["content_base64"]) == PDF


@respx.mock
@pytest.mark.parametrize(
    "status,error",
    [
        (400, UnexpectedProviderError),
        (401, NotAuthenticatedError),
        (403, PermissionDeniedError),
        (404, UnexpectedProviderError),
        (429, RateLimitedError),
    ],
)
def test_modern_file_statuses_fail_without_fallback(status: int, error: type[Exception]) -> None:
    route = mock_modern_course().mock(
        return_value=httpx.Response(status, text="fictional private response")
    )
    with pytest.raises(error) as caught:
        syllabus().fetch(COURSE, "11")
    assert "fictional private response" not in str(caught.value)
    assert route.call_count == 1 and len(respx.calls) == 3


@respx.mock
@pytest.mark.parametrize("location", [f"{BASE}/other", "https://elsewhere.invalid/file"])
def test_file_redirects_are_never_followed(location: str) -> None:
    mock_modern_course().mock(return_value=httpx.Response(302, headers={"location": location}))
    with pytest.raises(UnexpectedProviderError):
        syllabus().fetch(COURSE, "11")
    assert len(respx.calls) == 3


@respx.mock
@pytest.mark.parametrize(
    ("body", "declared"),
    [
        (b"<html>not a PDF</html>", "application/pdf"),
        (PDF, "text/html"),
        (b"PK\x03\x04bad", "application/octet-stream"),
        (b"\xff\xfe", "text/plain"),
    ],
)
def test_modern_file_rejects_invalid_type_or_structure(body: bytes, declared: str) -> None:
    mock_modern_course().mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": declared})
    )
    with pytest.raises(MalformedResponseError):
        syllabus().fetch(COURSE, "11")


@respx.mock
def test_modern_file_rejects_embedded_content_in_docx() -> None:
    mock_modern_course().mock(
        return_value=httpx.Response(
            200,
            content=docx(extra={"word/vbaProject.bin": b"fictional macro"}),
            headers={"content-type": DOCX_MEDIA_TYPE},
        )
    )
    with pytest.raises(MalformedResponseError, match="not a safe Word document"):
        syllabus().fetch(COURSE, "11")


@respx.mock
@pytest.mark.parametrize("length", ["not-a-number", str(len(PDF) + 5)])
def test_file_declared_length_must_be_valid_and_match_actual_bytes(length: str) -> None:
    mock_modern_course().mock(
        return_value=httpx.Response(
            200, content=PDF, headers={"content-type": "application/pdf", "content-length": length}
        )
    )
    with pytest.raises(MalformedResponseError, match="length"):
        syllabus().fetch(COURSE, "11")


@respx.mock
def test_unsolicited_partial_document_is_never_returned_as_complete() -> None:
    mock_modern_course().mock(
        return_value=httpx.Response(
            206,
            content=PDF[:8],
            headers={"content-type": "application/pdf", "content-range": "bytes 0-7/30"},
        )
    )
    with pytest.raises(UnexpectedProviderError, match="document status"):
        syllabus().fetch(COURSE, "11")


@respx.mock
def test_missing_file_length_is_safe_under_the_streaming_bound() -> None:
    mock_modern_course().mock(
        return_value=httpx.Response(
            200,
            content=iter([PDF[:8], PDF[8:]]),
            headers={"content-type": "application/pdf"},
        )
    )
    assert syllabus().fetch(COURSE, "11").content == PDF


@respx.mock
def test_modern_file_response_still_has_the_eight_mib_limit() -> None:
    mock_modern_course().mock(
        return_value=httpx.Response(
            200,
            content=PDF,
            headers={
                "content-type": "application/pdf",
                "content-length": str(MAX_DOCUMENT_BYTES + 1),
            },
        )
    )
    with pytest.raises(MalformedResponseError, match="safe size limit"):
        syllabus().fetch(COURSE, "11")


@respx.mock
@pytest.mark.parametrize(
    "item",
    [
        topic(11, "Syllabus", IsHidden=True),
        topic(11, "Syllabus", IsBroken=True),
        topic(11, "Syllabus", kind="Link"),
        topic(11, "Syllabus", url="/s.xlsx"),
        topic(11, "Syllabus", OrgUnitId=620001),
        topic(12, "Syllabus"),
    ],
)
def test_unlisted_or_unfetchable_topic_never_reaches_file_route(item: dict[str, Any]) -> None:
    route = mock_modern_course(item=item).mock(
        return_value=httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
    )
    with pytest.raises((ValueError, ProviderEndpointChangedError)):
        syllabus().fetch(COURSE, "11")
    assert route.call_count == 0


@respx.mock
def test_topic_url_and_filename_are_never_request_targets_or_output_names() -> None:
    hostile = topic(11, "..\\..\\Syllabus", url="https://elsewhere.invalid/secret.pdf")
    mock_modern_course(item=hostile).mock(
        return_value=httpx.Response(
            200,
            content=PDF,
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="../../danger.exe"',
            },
        )
    )
    document = syllabus().fetch(COURSE, "11")
    assert document.file_name == "Syllabus.pdf"
    assert len(respx.calls) == 3
    assert all(call.request.url.host == "example.brightspace.com" for call in respx.calls)


@respx.mock
def test_missing_authentication_stops_before_version_discovery() -> None:
    route = respx.get(f"{BASE}/d2l/api/versions/").mock(
        return_value=httpx.Response(
            200, json=[{"ProductCode": "le", "SupportedVersions": ["1.82"]}]
        )
    )

    def unavailable() -> dict[str, str]:
        raise NotAuthenticatedError("no fictional session")

    provider = BrightspaceContentProvider(D2LHttpClient(BASE, auth_header_provider=unavailable))
    with pytest.raises(NotAuthenticatedError):
        SyllabusService(provider).fetch(COURSE, "11")
    assert route.call_count == 0 and not respx.calls
