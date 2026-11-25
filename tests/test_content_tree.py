from __future__ import annotations

import pytest

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.providers.brightspace_content import (
    BrightspaceContentProvider,
    parse_content_tree,
    parse_toc,
    select_file_version,
    select_version,
)
from d2l_cli.syllabus_service import SyllabusService


def module(
    id: int, title: str, *, topics: list[dict] | None = None, modules: list[dict] | None = None
) -> dict:
    return {"ModuleId": id, "Title": title, "Topics": topics or [], "Modules": modules or []}


def topic(id: int, title: str = "Reading") -> dict:
    return {"TopicId": id, "Title": title, "TypeIdentifier": "File"}


def test_nested_content_keeps_identity_parent_and_order() -> None:
    raw = {
        "Modules": [
            module(
                1, "Week 1", topics=[topic(10)], modules=[module(2, "Lecture", topics=[topic(11)])]
            )
        ]
    }
    tree = parse_content_tree(raw, course_id="25")
    assert tree.course_id == "25" and tree.node_count == 4 and tree.completeness == "unknown"
    root = tree.modules[0]
    assert (root.id, root.parent_module_id) == ("1", None)
    assert [(c.id, c.parent_module_id) for c in root.children] == [("10", "1"), ("2", "1")]
    assert root.children[1].children[0].parent_module_id == "2"


@pytest.mark.parametrize(
    "raw",
    [
        {"Modules": [module(1, "A"), module(1, "B")]},
        {"Modules": [module(1, "A", topics=[topic(2), topic(2)])]},
        {"Modules": [module(1, "A", topics=[{"TopicId": 2, "Title": "X"}])]},
    ],
)
def test_malformed_or_duplicate_structure_fails_closed(raw: dict) -> None:
    with pytest.raises(ProviderEndpointChangedError):
        parse_content_tree(raw, course_id="25")


def test_cycle_fails_closed() -> None:
    a = module(1, "A")
    a["Modules"].append(a)
    with pytest.raises(ProviderEndpointChangedError):
        parse_content_tree({"Modules": [a]}, course_id="25")


def test_depth_limit_fails_closed() -> None:
    child = module(11, "Deep")
    for level in range(10, 0, -1):
        child = module(level, f"Level {level}", modules=[child])
    with pytest.raises(ProviderEndpointChangedError):
        parse_content_tree({"Modules": [child]}, course_id="25")


def test_hidden_content_is_excluded() -> None:
    hidden = module(1, "Hidden")
    hidden["IsHidden"] = True
    tree = parse_content_tree(
        {"Modules": [hidden, module(2, "Visible", topics=[topic(3)])]}, course_id="25"
    )
    assert tree.node_count == 2 and [m.id for m in tree.modules] == ["2"]


def test_empty_toc_is_unknown_not_complete() -> None:
    tree = parse_content_tree({"Modules": []}, course_id="25")
    assert tree.modules == () and tree.node_count == 0 and tree.completeness == "unknown"


def test_missing_child_array_and_oversize_id_fail_closed() -> None:
    for payload in (
        {"Modules": [{"ModuleId": 1, "Title": "X", "Topics": []}]},
        {"Modules": [module(10**20, "X")]},
        {"Modules": [], "Topics": [topic(1)]},
        {"Modules": [], "OrgUnitId": 26},
        {"Modules": [dict(module(1, "X"), OrgUnitId=26)]},
        {"Modules": [module(1, "X", topics=[dict(topic(2), OrgUnitId=26)])]},
    ):
        with pytest.raises(ProviderEndpointChangedError):
            parse_content_tree(payload, course_id="25")


def test_hidden_entries_still_count_toward_traversal_bound() -> None:
    hidden = [dict(module(i, "Hidden"), IsHidden=True) for i in range(10_001)]
    with pytest.raises(ProviderEndpointChangedError, match="NODE_LIMIT"):
        parse_content_tree({"Modules": hidden}, course_id="25")


def test_tree_provider_uses_only_versions_and_toc_gets() -> None:
    class Response:
        def __init__(self, body: object) -> None:
            self.json_body = body

    class Client:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def get(self, path: str) -> Response:
            self.paths.append(path)
            if path == "/d2l/api/versions/":
                return Response([{"ProductCode": "le", "SupportedVersions": ["1.74"]}])
            return Response({"Modules": [module(1, "Week", topics=[topic(2)])]})

    client = Client()
    result = BrightspaceContentProvider(client).list_tree("25")  # type: ignore[arg-type]
    assert result.node_count == 2
    assert client.paths == ["/d2l/api/versions/", "/d2l/api/le/1.74/25/content/toc"]


def test_titles_and_types_are_terminal_safe() -> None:
    raw = {
        "Modules": [
            module(
                1,
                "\x1b[31mWeek",
                topics=[dict(topic(2, "<b>Read</b>"), TypeIdentifier="File\x1b[0m")],
            )
        ]
    }
    node = parse_content_tree(raw, course_id="25").modules[0]
    assert "\x1b" not in node.title and "\x1b" not in node.children[0].kind
    assert node.children[0].title == "<b>Read</b>"  # inert plain text, not rendered HTML


def versions(*items: str) -> list[dict[str, object]]:
    return [{"ProductCode": "le", "SupportedVersions": list(items)}]


@pytest.mark.parametrize(
    ("advertised", "toc_version", "file_version"),
    [
        (("1.82",), "1.82", None),
        (("1.82", "1.83", "1.74"), "1.83", "1.74"),
        (("1.120", "1.121", "1.75"), "1.120", "1.75"),
        (("1.74", "1.75", "1.80"), "1.75", "1.75"),
        (("1.121", "1.122"), None, None),
        (("1.20", "1.29"), None, None),
        ((), None, None),
    ],
)
def test_toc_and_file_version_policies_are_independent(
    advertised: tuple[str, ...], toc_version: str | None, file_version: str | None
) -> None:
    payload = versions(*advertised)
    if toc_version is None:
        with pytest.raises(ProviderEndpointChangedError, match="CONTENT_VERSION_UNSUPPORTED"):
            select_version(payload)
    else:
        assert select_version(payload) == toc_version
    if file_version is None:
        with pytest.raises(ProviderEndpointChangedError, match="CONTENT_FILE_VERSION_UNSUPPORTED"):
            select_file_version(payload)
    else:
        assert select_file_version(payload) == file_version


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        [{"ProductCode": "le"}],
        [{"ProductCode": "le", "SupportedVersions": "1.82"}],
        versions("1.082"),
        versions("1.9999"),
        versions("2.0"),
        versions("1.82", 82),  # type: ignore[arg-type]
        [*versions("1.82"), *versions("1.74")],
        [*versions("1.82"), None],
    ],
)
def test_malformed_version_discovery_fails_closed(payload: object) -> None:
    with pytest.raises(ProviderEndpointChangedError, match="CONTENT_VERSIONS_SHAPE"):
        select_version(payload)


def test_non_le_product_does_not_authorize_toc() -> None:
    with pytest.raises(ProviderEndpointChangedError, match="CONTENT_VERSION_UNSUPPORTED"):
        select_version([{"ProductCode": "lp", "SupportedVersions": ["1.82"]}])


def test_modern_toc_get_never_implies_modern_file_get() -> None:
    class Response:
        def __init__(self, body: object) -> None:
            self.json_body = body

    class Client:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def get(self, path: str) -> Response:
            self.paths.append(path)
            if path == "/d2l/api/versions/":
                return Response(versions("1.82"))
            return Response(
                {"Modules": [module(1, "Week", topics=[dict(topic(2, "Syllabus"), Url="/s.pdf")])]}
            )

        def get_bytes(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("file GET must remain blocked")

    client = Client()
    provider = BrightspaceContentProvider(client)  # type: ignore[arg-type]
    assert provider.list_tree("25").node_count == 2
    search = SyllabusService(provider).find("25")
    assert search.api_version == "1.82" and search.recommended_topic_id is None
    assert len(search.candidates) == 1 and not search.candidates[0].fetchable
    assert "file_version_unsupported" in search.candidates[0].reasons
    with pytest.raises(ProviderEndpointChangedError, match="CONTENT_FILE_VERSION_UNSUPPORTED"):
        SyllabusService(provider).fetch("25", "2")
    assert client.paths == [
        "/d2l/api/versions/",
        "/d2l/api/le/1.82/25/content/toc",
        "/d2l/api/le/1.82/25/content/toc",
        "/d2l/api/le/1.82/25/content/toc",
    ]


def test_mixed_versions_use_modern_toc_and_legacy_file_route() -> None:
    class Response:
        json_body: object

        def __init__(self, body: object) -> None:
            self.json_body = body

    class FileResponse:
        content = b"%PDF-1.7\nfictional document\n"
        media_type = "application/pdf"

    class Client:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def get(self, path: str) -> Response:
            self.paths.append(path)
            if path == "/d2l/api/versions/":
                return Response(versions("1.74", "1.82"))
            return Response(
                {"Modules": [module(1, "Start", topics=[dict(topic(2, "Syllabus"), Url="/s.pdf")])]}
            )

        def get_bytes(self, path: str, **kwargs: object) -> FileResponse:
            self.paths.append(path)
            return FileResponse()

    client = Client()
    service = SyllabusService(BrightspaceContentProvider(client))  # type: ignore[arg-type]
    assert service.find("25").api_version == "1.82"
    assert service.fetch("25", "2").api_version == "1.74"
    assert client.paths == [
        "/d2l/api/versions/",
        "/d2l/api/le/1.82/25/content/toc",
        "/d2l/api/le/1.82/25/content/toc",
        "/d2l/api/le/1.74/25/content/topics/2/file",
    ]


@pytest.mark.parametrize(
    "raw",
    [
        {"Modules": [], "OrgUnitId": 26},
        {"Modules": [], "Topics": [topic(2)]},
        {"Modules": [dict(module(1, "X"), OrgUnitId=26)]},
        {"Modules": [module(1, "X", topics=[dict(topic(2), OrgUnitId=26)])]},
        {"Modules": [module(1, "X", topics=[dict(topic(2), Identifier="3")])]},
        {"Modules": [module(1, "X", topics=[topic(10**20)])]},
        {"Modules": [dict(module(1, "X"), ModuleId=True)]},
        {"Modules": [module(1, "X"), module(1, "Y")]},
        {"Modules": [{"ModuleId": 1, "Title": "X", "Topics": []}]},
    ],
)
def test_syllabus_toc_refuses_foreign_or_conflicting_file_topics(raw: dict) -> None:
    with pytest.raises(ProviderEndpointChangedError):
        parse_toc(raw, course_id="25")


def test_syllabus_toc_cycle_fails_closed() -> None:
    raw = module(1, "Cycle")
    raw["Modules"].append(raw)
    with pytest.raises(ProviderEndpointChangedError, match="CONTENT_MODULE_STRUCTURE"):
        parse_toc({"Modules": [raw]}, course_id="25")


def test_locked_topic_is_retained_and_external_url_is_inert() -> None:
    raw = module(
        1, "Week", topics=[dict(topic(2), IsLocked=True, Url="https://example.invalid/x.pdf")]
    )
    listed = parse_toc({"Modules": [raw]}, course_id="25")
    assert len(listed) == 1 and listed[0].file_extension == "pdf"
