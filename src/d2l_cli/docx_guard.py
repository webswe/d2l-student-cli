"""Decide, from the bytes alone, whether a downloaded file is a plain Word (``.docx``) document.

A syllabus is read as *data*. This module never extracts, renders, executes or follows anything in
the file; it only refuses what should not be handed on. The checks are all structural and bounded:

* the bytes must be a well-formed ZIP container (no filename or declared type is trusted);
* nothing is encrypted, and the container is small in member count and in total expansion, with a
  per-member and an overall compression-ratio ceiling (a "zip bomb" is refused before any member is
  inflated, and the two members that are read are read through a hard byte limit);
* the package must be an Office Open XML *wordprocessing document* (not a template, not
  macro-enabled, not another Office format);
* no macro project, ActiveX control, embedded object/package or other embedded program is present;
* the two XML parts that are inspected must not declare a DTD or entities.

External references (hyperlinks, linked images) are legitimate in a syllabus and are simply never
followed: this program makes no request on behalf of a document.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from typing import Final

from d2l_cli.client.errors import MalformedResponseError

DOCX_MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ZIP_MAGIC: Final = b"PK\x03\x04"

MAX_MEMBERS: Final = 1_000
MAX_MEMBER_BYTES: Final = 20 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 50 * 1024 * 1024
MAX_RATIO: Final = 100
_RATIO_FLOOR_BYTES: Final = 64 * 1024  # small parts may compress extremely well without risk
MAX_INSPECTED_PART_BYTES: Final = 5 * 1024 * 1024

_CONTENT_TYPES: Final = "[Content_Types].xml"
_MAIN_PART: Final = "word/document.xml"
_MAIN_CONTENT_TYPE: Final = "wordprocessingml.document.main+xml"
_FORBIDDEN_NAME_PARTS: Final = ("vbaproject", "/embeddings/", "/activex/", "/oleobject", "vbadata")
_FORBIDDEN_CONTENT_TYPES: Final = ("macroenabled", "vbaproject", "oleobject", "activex")
_FORBIDDEN_XML: Final = (b"<!doctype", b"<!entity")


def _refuse(reason: str) -> MalformedResponseError:
    return MalformedResponseError(f"document was not a safe Word document ({reason})")


def _read_part(archive: zipfile.ZipFile, name: str) -> bytes:
    with archive.open(name) as part:
        data = part.read(MAX_INSPECTED_PART_BYTES + 1)
    if len(data) > MAX_INSPECTED_PART_BYTES:
        raise _refuse("a part is too large to inspect")
    return data


def _check_container(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise _refuse("too many parts")
    total = 0
    for info in infos:
        name = info.filename
        lowered = name.lower()
        if info.flag_bits & 0x1:
            raise _refuse("encrypted")
        if name.startswith(("/", "\\")) or ".." in name.split("/") or "\\" in name:
            raise _refuse("unsafe part name")
        if any(part in f"/{lowered}" for part in _FORBIDDEN_NAME_PARTS):
            raise _refuse("macros or embedded objects")
        if info.file_size > MAX_MEMBER_BYTES:
            raise _refuse("a part expands too far")
        if (
            info.file_size > _RATIO_FLOOR_BYTES
            and info.compress_size > 0
            and info.file_size / info.compress_size > MAX_RATIO
        ):
            raise _refuse("suspicious compression")
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise _refuse("expands too far")


def validate_docx(content: bytes) -> None:
    """Raise ``MalformedResponseError`` unless ``content`` is a safe, plain Word document."""
    if not content.startswith(ZIP_MAGIC):
        raise _refuse("not a ZIP container")
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile:
        raise _refuse("malformed container") from None
    with archive:
        _check_container(archive)
        names = set(archive.namelist())
        if _CONTENT_TYPES not in names or _MAIN_PART not in names:
            raise _refuse("not a wordprocessing document")
        try:
            types = _read_part(archive, _CONTENT_TYPES)
            main = _read_part(archive, _MAIN_PART)
        except (zipfile.BadZipFile, OSError, EOFError, RuntimeError, NotImplementedError):
            raise _refuse("unreadable part") from None
    lowered_types = types.lower()
    if _MAIN_CONTENT_TYPE.encode() not in lowered_types or b".template" in lowered_types:
        raise _refuse("not a plain document")
    if any(token.encode() in lowered_types for token in _FORBIDDEN_CONTENT_TYPES):
        raise _refuse("macros or embedded objects")
    for part in (types, main):
        if b"\x00" in part[:64]:  # UTF-16/32 would hide a declaration from the ASCII scan below
            raise _refuse("unexpected text encoding")
        head = part[:4096].lower()
        if any(token in head for token in _FORBIDDEN_XML):
            raise _refuse("unexpected markup declarations")
