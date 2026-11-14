"""Word (.docx) syllabus retrieval: a safe, structural allowlist and nothing more.

Every document here is built in memory from fictional text. The checks under test never open or
interpret the document's content beyond two bounded, declaration-free XML parts, and the service
never follows a reference found inside a file.
"""

from __future__ import annotations

import hashlib
import zipfile
from io import BytesIO
from typing import Any

import httpx
import pytest
import respx

from d2l_cli.client.errors import MalformedResponseError
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.docx_guard import DOCX_MEDIA_TYPE, MAX_MEMBERS, validate_docx
from d2l_cli.providers.brightspace_content import BrightspaceContentProvider
from d2l_cli.syllabus_service import SyllabusService
from tests.test_syllabus_discovery import BASE, COURSE, VERSIONS, client, module, toc, topic

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/></Types>'
)
DOCUMENT = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>Grading: Homework 40%</w:t></w:r></w:p></w:body></w:document>"
)


def docx(
    *,
    content_types: str = CONTENT_TYPES,
    document: str = DOCUMENT,
    extra: dict[str, bytes] | None = None,
    skip: tuple[str, ...] = (),
) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if "[Content_Types].xml" not in skip:
            archive.writestr("[Content_Types].xml", content_types)
        if "word/document.xml" not in skip:
            archive.writestr("word/document.xml", document)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def rejected(content: bytes, reason: str) -> None:
    with pytest.raises(MalformedResponseError, match="not a safe Word document") as caught:
        validate_docx(content)
    assert reason in str(caught.value)


def test_a_plain_word_document_is_accepted() -> None:
    validate_docx(docx())


def test_a_hyperlinked_document_is_accepted_and_the_link_is_never_followed() -> None:
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="r1" Type="hyperlink" Target="http://example.test/x" '
        'TargetMode="External"/></Relationships>'
    )
    validate_docx(docx(extra={"word/_rels/document.xml.rels": rels.encode()}))


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"not a zip at all", "not a ZIP container"),
        (b"PK\x03\x04" + b"\x00" * 40, "malformed container"),
        (docx(skip=("word/document.xml",)), "not a wordprocessing document"),
        (docx(skip=("[Content_Types].xml",)), "not a wordprocessing document"),
    ],
)
def test_non_documents_and_other_packages_are_refused(content: bytes, reason: str) -> None:
    rejected(content, reason)


def test_a_spreadsheet_or_presentation_package_is_not_a_word_document() -> None:
    sheet_types = CONTENT_TYPES.replace(
        "wordprocessingml.document.main+xml", "spreadsheetml.sheet.main+xml"
    )
    rejected(docx(content_types=sheet_types), "not a plain document")


@pytest.mark.parametrize("token", ["macroEnabled.main+xml", "vbaProject", "oleObject", "activeX"])
def test_macro_enabled_and_embedded_object_content_types_are_refused(token: str) -> None:
    types = CONTENT_TYPES.replace(
        "</Types>", f'<Default Extension="bin" ContentType="x/{token}"/></Types>'
    )
    rejected(docx(content_types=types), "macros or embedded objects")


@pytest.mark.parametrize(
    "name",
    [
        "word/vbaProject.bin",
        "word/embeddings/oleObject1.bin",
        "word/activeX/activeX1.xml",
        "customUI/VBAData.xml",
    ],
)
def test_macro_projects_and_embedded_objects_are_refused_by_part_name(name: str) -> None:
    rejected(docx(extra={name: b"x"}), "macros or embedded objects")


def test_an_encrypted_member_is_refused() -> None:
    raw = bytearray(docx())
    # Set the "encrypted" general-purpose flag (bit 0) on the first local file header and the
    # central directory entry; a reader must see the flag, not guess from the content.
    for marker, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = raw.find(marker)
        assert start >= 0
        raw[start + offset] |= 0x01
    rejected(bytes(raw), "encrypted")


def test_too_many_parts_are_refused() -> None:
    many = {f"word/media/{i}.xml": b"x" for i in range(MAX_MEMBERS + 1)}
    rejected(docx(extra=many), "too many parts")


def test_a_compression_bomb_is_refused_before_anything_is_inflated() -> None:
    bomb = docx(extra={"word/media/zeros.bin": b"\x00" * (30 * 1024 * 1024)})
    assert len(bomb) < 200_000  # tiny on the wire, huge when inflated
    with pytest.raises(MalformedResponseError, match="not a safe Word document"):
        validate_docx(bomb)


def test_a_part_name_that_escapes_the_package_is_refused() -> None:
    rejected(docx(extra={"../evil.xml": b"x"}), "unsafe part name")


@pytest.mark.parametrize(
    "declaration",
    ['<!DOCTYPE d [<!ENTITY a "aaaa">]>', "<!ENTITY a SYSTEM 'file:///c:/x'>"],
)
def test_an_xml_declaration_that_defines_entities_is_refused(declaration: str) -> None:
    document = DOCUMENT.replace("<w:document", declaration + "<w:document", 1)
    rejected(docx(document=document), "unexpected markup declarations")


def test_utf16_xml_cannot_hide_a_declaration() -> None:
    document = DOCUMENT.replace("<w:document", '<!DOCTYPE d [<!ENTITY a "x">]><w:document', 1)
    rejected(docx(document=document.encode("utf-16").decode("latin-1")), "text encoding")


# --- through the service and the HTTP boundary ----------------------------------------------


def service() -> SyllabusService:
    return SyllabusService(BrightspaceContentProvider(client()))


def mock_word_course(title: str = "Course Syllabus") -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=VERSIONS))
    topics: list[dict[str, Any]] = [topic(11, title, url="/content/enforced/1/syllabus.docx")]
    respx.get(f"{BASE}/d2l/api/le/1.74/{COURSE}/content/toc").mock(
        return_value=httpx.Response(200, json=toc(module("Start Here", topics)))
    )


@respx.mock
@pytest.mark.parametrize("declared", [DOCX_MEDIA_TYPE, "application/octet-stream"])
def test_fetch_returns_a_verified_word_document_with_provenance(declared: str) -> None:
    mock_word_course()
    content = docx()
    route = respx.get(f"{BASE}/d2l/api/le/1.74/{COURSE}/content/topics/11/file").mock(
        return_value=httpx.Response(200, content=content, headers={"content-type": declared})
    )
    document = service().fetch(COURSE, "11")
    assert document.media_type == DOCX_MEDIA_TYPE
    assert document.file_name == "Course Syllabus.docx"
    assert document.content == content and document.sha256 == hashlib.sha256(content).hexdigest()
    assert document.module_path == ("Start Here",) and document.topic_id == "11"
    assert route.call_count == 1
    assert all(call.request.method == "GET" for call in respx.calls)
    assert not any("example.test" in str(call.request.url) for call in respx.calls)


@respx.mock
def test_a_file_named_docx_that_is_not_a_zip_is_refused() -> None:
    mock_word_course()
    respx.get(f"{BASE}/d2l/api/le/1.74/{COURSE}/content/topics/11/file").mock(
        return_value=httpx.Response(
            200, content=b"MZ\x90\x00 an executable", headers={"content-type": DOCX_MEDIA_TYPE}
        )
    )
    with pytest.raises(MalformedResponseError, match="did not match"):
        service().fetch(COURSE, "11")


@respx.mock
def test_a_word_document_declared_as_a_pdf_or_text_is_a_mismatch() -> None:
    mock_word_course()
    route = respx.get(f"{BASE}/d2l/api/le/1.74/{COURSE}/content/topics/11/file")
    for declared in ("application/pdf", "text/plain"):
        route.mock(
            return_value=httpx.Response(200, content=docx(), headers={"content-type": declared})
        )
        with pytest.raises(MalformedResponseError):
            service().fetch(COURSE, "11")


@respx.mock
def test_a_macro_document_is_refused_even_when_listed_and_declared_correctly() -> None:
    mock_word_course()
    respx.get(f"{BASE}/d2l/api/le/1.74/{COURSE}/content/topics/11/file").mock(
        return_value=httpx.Response(
            200,
            content=docx(extra={"word/vbaProject.bin": b"macro"}),
            headers={"content-type": DOCX_MEDIA_TYPE},
        )
    )
    with pytest.raises(MalformedResponseError, match="macros or embedded objects"):
        service().fetch(COURSE, "11")


def test_the_http_client_allows_the_word_media_type_and_no_other_office_type() -> None:
    from d2l_cli.providers import brightspace_content as content_module

    allowed = content_module._ALLOWED_FILE_TYPES
    assert DOCX_MEDIA_TYPE in allowed
    assert not any("spreadsheetml" in t or "presentationml" in t or "msword" in t for t in allowed)
    assert isinstance(client(), D2LHttpClient)
