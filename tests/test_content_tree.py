from __future__ import annotations

import pytest

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.providers.brightspace_content import BrightspaceContentProvider, parse_content_tree


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


@pytest.mark.parametrize("raw", [
    {"Modules": [module(1, "A"), module(1, "B")]},
    {"Modules": [module(1, "A", topics=[topic(2), topic(2)])]},
    {"Modules": [module(1, "A", topics=[{"TopicId": 2, "Title": "X"}])]},
])
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
