"""GET-only provider for a course's content table of contents and its downloadable files.

Contract source: the documented Brightspace Valence *Learning Environment* content API --
``GET /d2l/api/versions/``, ``GET /d2l/api/le/{version}/{orgUnitId}/content/toc`` and
``GET /d2l/api/le/{version}/{orgUnitId}/content/topics/{topicId}/file``. It is the same API
family the browser already calls with the session cookie for ``/d2l/api/lp/1.47/...`` (see
docs/PROVIDER_NOTES.md). It has NOT yet been confirmed against the live tenant; see
docs/SYLLABUS_DISCOVERY.md for the value-free check that does so.

Fail-closed: any shape that deviates from the documented one raises
``ProviderEndpointChangedError`` instead of yielding partial data. The ``Url`` field of a topic is
read only for its file extension and is never requested; files are fetched solely through the
documented ``topics/{id}/file`` route, and only for a topic id that the course's own table of
contents lists as a visible file, so an id cannot be probed.
"""

from __future__ import annotations

import re
from typing import Any

from d2l_cli.announcement_sanitizer import sanitize_plain_text
from d2l_cli.client.errors import MalformedResponseError, ProviderEndpointChangedError
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.models.content import ContentNode, ContentTree
from d2l_cli.models.syllabus import ContentTopic, TopicKind

_VERSIONS_PATH = "/d2l/api/versions/"
_PRODUCT_CODE = "le"
#: The public contract documents both GET routes in the current range. Keep selectors
#: route-specific so future route changes cannot silently widen the other capability.
_TOC_MODERN_RANGE = (82, 120)
_FILE_MODERN_RANGE = (82, 120)
_LEGACY_RANGE = (30, 75)
_VERSION_RE = re.compile(r"1\.([0-9]{1,3})")
_UNSET = object()

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
_ALLOWED_FILE_TYPES = frozenset(
    {
        "application/pdf",
        "text/plain",
        "application/octet-stream",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)

_MAX_DEPTH = 10
_MAX_NODES = 10_000
_MAX_TITLE_CHARS = 500
_MAX_EXTENSION_CHARS = 8
_EXTENSION_RE = re.compile(r"[a-z0-9]{1,8}")


def _changed(code: str) -> ProviderEndpointChangedError:
    return ProviderEndpointChangedError(
        f"Course content structure is unsupported (CONTENT_{code.upper()})"
    )


def _is_numeric_id(value: str) -> bool:
    return bool(value) and value.isascii() and value.isdecimal() and len(value) <= 20


def _title(value: object, code: str) -> str:
    if not isinstance(value, str):
        raise _changed(code)
    cleaned = sanitize_plain_text(value, preserve_lines=False)
    return cleaned[:_MAX_TITLE_CHARS]


def _optional_flag(node: dict[str, Any], key: str, code: str) -> bool:
    value = node.get(key, False)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise _changed(code)
    return value


def _optional_date(node: dict[str, Any]) -> str | None:
    value = node.get("LastModifiedDate")
    return value[:40] if isinstance(value, str) and value else None


def _extension_of(url: object) -> str | None:
    """The file extension a topic's ``Url`` advertises. The URL itself is never followed."""
    if not isinstance(url, str):
        return None
    name = url.split("?", 1)[0].rsplit("/", 1)[-1]
    if "." not in name:
        return None
    extension = name.rsplit(".", 1)[-1].lower()
    return extension if _EXTENSION_RE.fullmatch(extension) else None


def _supported_le_versions(payload: object) -> set[int]:
    if not isinstance(payload, list):
        raise _changed("VERSIONS_SHAPE")
    found: set[int] | None = None
    for entry in payload:
        if not isinstance(entry, dict):
            raise _changed("VERSIONS_SHAPE")
        if entry.get("ProductCode") != _PRODUCT_CODE:
            continue
        if found is not None:
            raise _changed("VERSIONS_SHAPE")
        supported = entry.get("SupportedVersions")
        if not isinstance(supported, list) or not all(isinstance(v, str) for v in supported):
            raise _changed("VERSIONS_SHAPE")
        found = set()
        for version in supported:
            match = _VERSION_RE.fullmatch(version)
            if match is None or str(int(match.group(1))) != match.group(1):
                raise _changed("VERSIONS_SHAPE")
            found.add(int(match.group(1)))
    if found is None:
        raise _changed("VERSION_UNSUPPORTED")
    return found


def _newest_in_range(versions: set[int], lower: int, upper: int, code: str) -> str:
    usable = [minor for minor in versions if lower <= minor <= upper]
    if not usable:
        raise _changed(code)
    return f"1.{max(usable)}"


def select_version(payload: object) -> str:
    """Select a bounded TOC version, preferring current versions over legacy versions."""
    versions = _supported_le_versions(payload)
    modern = [v for v in versions if _TOC_MODERN_RANGE[0] <= v <= _TOC_MODERN_RANGE[1]]
    if modern:
        return f"1.{max(modern)}"
    return _newest_in_range(versions, *_LEGACY_RANGE, "VERSION_UNSUPPORTED")


def select_file_version(payload: object) -> str:
    """Select a documented file-route version, preferring the bounded current range."""
    versions = _supported_le_versions(payload)
    modern = [v for v in versions if _FILE_MODERN_RANGE[0] <= v <= _FILE_MODERN_RANGE[1]]
    if modern:
        return f"1.{max(modern)}"
    return _newest_in_range(versions, *_LEGACY_RANGE, "FILE_VERSION_UNSUPPORTED")


def parse_toc(payload: object, *, course_id: str) -> tuple[ContentTopic, ...]:
    """Flatten the documented table of contents into visible topics with their module trail.

    Hidden modules and hidden or broken topics are not visible to a student and are dropped.
    """
    if not _is_numeric_id(course_id):
        raise ValueError("course_id must be a non-empty numeric Brightspace org-unit id")
    if not isinstance(payload, dict) or not isinstance(payload.get("Modules"), list):
        raise _changed("TOC_SHAPE")
    root_topics = payload.get("Topics", [])
    if not isinstance(root_topics, list) or root_topics:
        raise _changed("ROOT_TOPICS")

    def check_course(node: dict[str, Any]) -> None:
        owner = node.get("OrgUnitId")
        if owner is not None and (isinstance(owner, bool) or str(owner) != course_id):
            raise _changed("COURSE_MISMATCH")

    check_course(payload)

    topics: list[ContentTopic] = []
    seen: set[str] = set()
    module_ids: set[str] = set()
    active: set[int] = set()
    nodes = 0

    def walk(modules: list[Any], trail: tuple[str, ...], depth: int) -> None:
        nonlocal nodes
        if depth > _MAX_DEPTH:
            raise _changed("DEPTH_LIMIT")
        for module in modules:
            nodes += 1
            if nodes > _MAX_NODES or not isinstance(module, dict):
                raise _changed("NODE_LIMIT" if nodes > _MAX_NODES else "MODULE_SHAPE")
            if id(module) in active:
                raise _changed("MODULE_STRUCTURE")
            active.add(id(module))
            try:
                check_course(module)
                module_id = module.get("ModuleId")
                if (
                    isinstance(module_id, bool)
                    or not isinstance(module_id, int)
                    or module_id < 0
                    or len(str(module_id)) > 20
                ):
                    raise _changed("NODE_ID")
                if _optional_flag(module, "IsHidden", "MODULE_FLAG"):
                    continue
                if str(module_id) in module_ids:
                    raise _changed("DUPLICATE_MODULE")
                module_ids.add(str(module_id))
                here = (*trail, _title(module.get("Title"), "MODULE_TITLE"))
                child_modules = module.get("Modules")
                child_topics = module.get("Topics")
                if not isinstance(child_modules, list) or not isinstance(child_topics, list):
                    raise _changed("MODULE_SHAPE")
                for topic in child_topics:
                    nodes += 1
                    if nodes > _MAX_NODES or not isinstance(topic, dict):
                        raise _changed("NODE_LIMIT" if nodes > _MAX_NODES else "TOPIC_SHAPE")
                    check_course(topic)
                    topic_id = topic.get("TopicId")
                    if (
                        isinstance(topic_id, bool)
                        or not isinstance(topic_id, int)
                        or topic_id < 0
                        or len(str(topic_id)) > 20
                    ):
                        raise _changed("TOPIC_ID")
                    identifier = topic.get("Identifier")
                    if identifier is not None and identifier != str(topic_id):
                        raise _changed("TOPIC_ID")
                    kind_name = topic.get("TypeIdentifier")
                    if not isinstance(kind_name, str):
                        raise _changed("TOPIC_TYPE")
                    if _optional_flag(topic, "IsHidden", "TOPIC_FLAG") or _optional_flag(
                        topic, "IsBroken", "TOPIC_FLAG"
                    ):
                        continue
                    if str(topic_id) in seen:
                        raise _changed("DUPLICATE_TOPIC")
                    seen.add(str(topic_id))
                    is_file = kind_name.strip().lower() == "file"
                    topics.append(
                        ContentTopic(
                            course_id=course_id,
                            topic_id=str(topic_id),
                            title=_title(topic.get("Title"), "TOPIC_TITLE"),
                            module_path=here,
                            kind=TopicKind.FILE if is_file else TopicKind.OTHER,
                            file_extension=_extension_of(topic.get("Url")) if is_file else None,
                            last_modified=_optional_date(topic),
                        )
                    )
                walk(child_modules, here, depth + 1)
            finally:
                active.remove(id(module))

    walk(payload["Modules"], (), 1)
    return tuple(topics)


def parse_content_tree(payload: object, *, course_id: str) -> ContentTree:
    """Keep visible module/topic identity and hierarchy from the established TOC route."""
    if not _is_numeric_id(course_id):
        raise ValueError("course_id must be a non-empty numeric Brightspace org-unit id")
    if not isinstance(payload, dict) or not isinstance(payload.get("Modules"), list):
        raise _changed("TOC_SHAPE")
    root_topics = payload.get("Topics", [])
    if not isinstance(root_topics, list) or root_topics:
        # This schema has no supported parent for a top-level topic.
        raise _changed("ROOT_TOPICS")
    module_ids: set[str] = set()
    topic_ids: set[str] = set()
    active_objects: set[int] = set()
    count = 0
    visited = 0

    def check_course(node: dict[str, Any]) -> None:
        owner = node.get("OrgUnitId")
        if owner is not None and (isinstance(owner, bool) or str(owner) != course_id):
            raise _changed("COURSE_MISMATCH")

    check_course(payload)

    def numeric_id(node: dict[str, Any], field: str) -> str:
        value = node.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or len(str(value)) > 20
        ):
            raise _changed("NODE_ID")
        return str(value)

    def visit_module(raw: object, parent: str | None, depth: int) -> ContentNode | None:
        nonlocal count, visited
        if depth > _MAX_DEPTH or not isinstance(raw, dict) or id(raw) in active_objects:
            raise _changed("MODULE_STRUCTURE")
        active_objects.add(id(raw))
        try:
            visited += 1
            if visited > _MAX_NODES:
                raise _changed("NODE_LIMIT")
            check_course(raw)
            if _optional_flag(raw, "IsHidden", "MODULE_FLAG"):
                return None
            count += 1
            module_id = numeric_id(raw, "ModuleId")
            if module_id in module_ids:
                raise _changed("DUPLICATE_MODULE")
            module_ids.add(module_id)
            nested = raw.get("Modules")
            topics = raw.get("Topics")
            if not isinstance(nested, list) or not isinstance(topics, list):
                raise _changed("MODULE_SHAPE")
            children: list[ContentNode] = []
            for topic in topics:
                visited += 1
                if visited > _MAX_NODES:
                    raise _changed("NODE_LIMIT")
                if not isinstance(topic, dict):
                    raise _changed("TOPIC_SHAPE")
                check_course(topic)
                if _optional_flag(topic, "IsHidden", "TOPIC_FLAG") or _optional_flag(
                    topic, "IsBroken", "TOPIC_FLAG"
                ):
                    continue
                count += 1
                topic_id = numeric_id(topic, "TopicId")
                if topic_id in topic_ids:
                    raise _changed("DUPLICATE_TOPIC")
                topic_ids.add(topic_id)
                identifier = topic.get("Identifier")
                if identifier is not None and identifier != topic_id:
                    raise _changed("TOPIC_ID")
                kind = topic.get("TypeIdentifier")
                if not isinstance(kind, str):
                    raise _changed("TOPIC_TYPE")
                children.append(
                    ContentNode(
                        topic_id,
                        _title(topic.get("Title"), "TOPIC_TITLE"),
                        module_id,
                        _title(kind, "TOPIC_TYPE"),
                    )
                )
            for child in nested:
                item = visit_module(child, module_id, depth + 1)
                if item is not None:
                    children.append(item)
            return ContentNode(
                module_id,
                _title(raw.get("Title"), "MODULE_TITLE"),
                parent,
                "module",
                tuple(children),
            )
        finally:
            active_objects.remove(id(raw))

    modules = tuple(
        node for raw in payload["Modules"] if (node := visit_module(raw, None, 1)) is not None
    )
    return ContentTree(course_id, modules, count)


class BrightspaceContentProvider:
    """Read the course table of contents and download one listed course file."""

    def __init__(self, client: D2LHttpClient) -> None:
        self._client = client
        self._versions: object = _UNSET

    def _version_payload(self) -> object:
        if self._versions is _UNSET:
            self._versions = self._client.get(_VERSIONS_PATH).json_body
        return self._versions

    def api_version(self) -> str:
        return select_version(self._version_payload())

    def file_api_version(self) -> str:
        return select_file_version(self._version_payload())

    def list_topics(self, course_id: str) -> tuple[ContentTopic, ...]:
        if not _is_numeric_id(course_id):
            raise ValueError("course_id must be a non-empty numeric Brightspace org-unit id")
        path = f"/d2l/api/le/{self.api_version()}/{course_id}/content/toc"
        response = self._client.get(path)
        return parse_toc(response.json_body, course_id=course_id)

    def list_tree(self, course_id: str) -> ContentTree:
        if not _is_numeric_id(course_id):
            raise ValueError("course_id must be a non-empty numeric Brightspace org-unit id")
        path = f"/d2l/api/le/{self.api_version()}/{course_id}/content/toc"
        return parse_content_tree(self._client.get(path).json_body, course_id=course_id)

    def download_topic_file(self, course_id: str, topic_id: str) -> tuple[bytes, str]:
        """Bytes and declared media type of one file topic (the caller proves it is listed)."""
        if not _is_numeric_id(course_id) or not _is_numeric_id(topic_id):
            raise ValueError("course and topic ids must be non-empty numeric ids")
        path = f"/d2l/api/le/{self.file_api_version()}/{course_id}/content/topics/{topic_id}/file"
        response = self._client.get_bytes(
            path, allowed_media_types=_ALLOWED_FILE_TYPES, max_bytes=MAX_DOCUMENT_BYTES
        )
        if not response.content:
            raise MalformedResponseError("provider returned an empty document")
        return response.content, response.media_type
