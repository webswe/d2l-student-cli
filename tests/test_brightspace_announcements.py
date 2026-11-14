from __future__ import annotations

from collections.abc import Mapping

import httpx
import pytest
import respx

from d2l_cli.client.errors import (
    MalformedResponseError,
    NetworkUnavailableError,
    NotAuthenticatedError,
    PermissionDeniedError,
    ProviderEndpointChangedError,
    UnexpectedProviderError,
)
from d2l_cli.client.http import D2LHttpClient, D2LTextResponse
from d2l_cli.models.announcement import AnnouncementCompleteness
from d2l_cli.providers.brightspace_announcements import (
    BrightspaceAnnouncementProvider,
    parse_announcement_list_html,
)

_BASE = "https://example.brightspace.com"
_NEWS_PATH = "/d2l/lms/news/main.d2l"
_COURSE_ID = "620000"


def _item_rows(
    announcement_id: str,
    title: str,
    body_html: str,
    *,
    course_id: str = _COURSE_ID,
) -> str:
    return f"""
    <tr><th><a href="/d2l/le/news/{course_id}/{announcement_id}/view?ou={course_id}">
      {title}</a></th><td>Fictional displayed date</td></tr>
    <tr><td colspan="2"><d2l-html-block html="{body_html}"></d2l-html-block></td></tr>
    """


def _page(*rows: str) -> str:
    return f"""
    <html><body><form method="POST" action="main.d2l?ou={_COURSE_ID}">
      <table>
        <tr><td colspan="2">Unrelated fictional page controls</td></tr>
        {''.join(rows)}
        <tr><td colspan="2">Unrelated fictional footer</td></tr>
      </table>
    </form></body></html>
    """


_VALID_HTML = _page(
    _item_rows(
        "501",
        "Fictional Update Alpha",
        "&lt;p&gt;First paragraph.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;One&lt;/li&gt;&lt;/ul&gt;",
    ),
    _item_rows("502", "Fictional Update Beta", ""),
)


def test_parser_returns_supported_items_in_provider_order() -> None:
    page = parse_announcement_list_html(_VALID_HTML, course_id=_COURSE_ID)

    assert [item.announcement_id for item in page.items] == ["501", "502"]
    first, second = page.items
    assert first.course_id == _COURSE_ID
    assert first.title == "Fictional Update Alpha"
    assert first.body == "First paragraph.\n- One"
    assert first.detail_reference == "/d2l/le/news/620000/501/view"
    assert second.title == "Fictional Update Beta"
    assert second.body is None
    assert page.completeness is AnnouncementCompleteness.UNKNOWN


def test_parser_sanitizes_body_and_title_terminal_controls() -> None:
    rows = _item_rows(
        "501",
        "\x1b[31mFictional Title\x1b[0m",
        "&lt;p onclick=&quot;run()&quot;&gt;Safe &amp;amp; readable&lt;/p&gt;"
        "&lt;script&gt;PRIVATE_SCRIPT&lt;/script&gt;"
        "&lt;iframe src=&quot;https://external.invalid&quot;&gt;PRIVATE_FRAME&lt;/iframe&gt;",
    )

    item = parse_announcement_list_html(_page(rows), course_id=_COURSE_ID).items[0]

    assert item.title == "Fictional Title"
    assert item.body == "Safe & readable"
    assert "PRIVATE" not in item.body
    assert "https://" not in item.body


def test_parser_rejects_hidden_markup_inside_title_link() -> None:
    html = _page(
        """
        <tr><th><a href="/d2l/le/news/620000/501/view?ou=620000">Visible
          <span style="display: none">Hidden title text</span></a></th><td>Date</td></tr>
        <tr><td colspan="2"><d2l-html-block html="Body"></d2l-html-block></td></tr>
        """
    )

    with pytest.raises(ProviderEndpointChangedError, match="UNSAFE_TITLE_MARKUP"):
        parse_announcement_list_html(html, course_id=_COURSE_ID)


def test_parser_allows_a_structurally_valid_empty_listing() -> None:
    page = parse_announcement_list_html(_page(), course_id=_COURSE_ID)

    assert page.items == ()
    assert page.completeness is AnnouncementCompleteness.UNKNOWN


@pytest.mark.parametrize(
    ("html", "code"),
    [
        (
            """
            <form method="post" action="main.d2l?ou=620000"><table>
                <tr><th><a href="/d2l/le/news/620000/501/view?ou=620000">Title</a></th>
                    <td>Date</td></tr>
            </table></form>
            """,
            "MISSING_CONTENT_ROW",
        ),
        (
            _page(
                "<tr><td colspan='2'><d2l-html-block html='Body'></d2l-html-block></td></tr>",
                _item_rows("501", "Title", "Body"),
            ),
            "ORPHAN_CONTENT_ROW",
        ),
        (
            _page(
                """
                <tr><th><a href="/d2l/le/news/620000/501/view?ou=620000">Title</a></th>
                    <td>Date</td></tr>
                <tr><td colspan="3"><d2l-html-block html="Body"></d2l-html-block></td></tr>
                """
            ),
            "CONTENT_ROW_GEOMETRY",
        ),
        (
            _page(
                """
                <tr><th rowspan="2"><a href="/d2l/le/news/620000/501/view?ou=620000">
                    Title</a></th><td>Date</td></tr>
                <tr><td colspan="2"><d2l-html-block html="Body"></d2l-html-block></td></tr>
                """
            ),
            "ITEM_SPAN",
        ),
        (
            _page(
                """
                <tr><th><a href="/d2l/le/news/620000/501/view?ou=620000">One</a>
                    <a href="/d2l/le/news/620000/502/view?ou=620000">Two</a></th>
                    <td>Date</td></tr>
                <tr><td colspan="2"><d2l-html-block html="Body"></d2l-html-block></td></tr>
                """
            ),
            "ITEM_LINK_COUNT",
        ),
        (
            _page(
                """
                <tr><th><a href="/d2l/le/news/620000/501/view?ou=620000">Title</a></th>
                    <td>Date</td></tr>
                <tr><td colspan="2"><d2l-html-block html="One"></d2l-html-block>
                    <d2l-html-block html="Two"></d2l-html-block></td></tr>
                """
            ),
            "CONTENT_BLOCK_COUNT",
        ),
        (
            _page(
                _item_rows("501", "One", "Body"),
                _item_rows("501", "Duplicate", "Body"),
            ),
            "DUPLICATE_IDENTITY",
        ),
        (
            _page(_item_rows("501", "Title", "Body", course_id="999999")),
            "COURSE_ASSOCIATION",
        ),
        (
            _page(
                """
                <tr class="pinned"><th><a href="/d2l/le/news/620000/501/view?ou=620000">
                    Title</a></th><td>Date</td></tr>
                <tr><td colspan="2"><d2l-html-block html="Body"></d2l-html-block></td></tr>
                """
            ),
            "SPECIAL_ITEM_ROW",
        ),
        (
            _page(
                """
                <tr><th><a href="/d2l/le/news/620000/501/view?ou=620000">Title</a></th>
                    <td>Date</td></tr>
                <tr><td colspan="2"><d2l-html-block html="Body"></d2l-html-block>
                    <table><tr><td><d2l-html-block html="Competing"></d2l-html-block>
                    </td></tr></table></td></tr>
                """
            ),
            "CONTENT_NESTED_ANNOUNCEMENT_STRUCTURE",
        ),
        (
            "<html><body><table><tr><td>Different page</td></tr></table></body></html>",
            "PAGE_FORM",
        ),
        (
            """
            <form method="post" action="main.d2l?ou=999999">
              <table><tr><td>Empty</td></tr></table>
            </form>
            """,
            "COURSE_ASSOCIATION",
        ),
        (
            """
            <form method="post" action="https://external.invalid/main.d2l?ou=620000">
              <table><tr><td>Empty</td></tr></table>
            </form>
            """,
            "PAGE_FORM_ACTION",
        ),
        (
            """
            <form method="post" action="main.d2l?ou=620000"><table><tr><th>
              <a href="/d2l/le/news/620000/501/view?ou=620000"
                 href="/d2l/le/news/620000/502/view?ou=620000">Title</a>
              </th><td>Date</td></tr>
            </table></form>
            """,
            "DETAIL_LINK",
        ),
        (
            """
            <form method="post" action="main.d2l?ou=620000"><table>
              <tr><th><a href="/d2l/le/news/620000/501/view?ou=620000">Title
            """,
            "MALFORMED_HTML",
        ),
        (
            _page(
                """
                <tr><th><a href="/d2l/le/news/620000/501/view?ou=620000">Title</a></th>
                    <td>Date</td></tr>
                <tr><td colspan="2"><d2l-html-block></d2l-html-block></td></tr>
                """
            ),
            "CONTENT_ATTRIBUTE",
        ),
        (
            _page(
                """
                <tr><th><a href="/d2l/le/news/620000/501/view?ou=620000"> </a></th>
                    <td>Date</td></tr>
                <tr><td colspan="2"><d2l-html-block html="Body"></d2l-html-block></td></tr>
                """
            ),
            "EMPTY_TITLE",
        ),
        (
            _page(
                _item_rows("501", "Title", "Body"),
                '<tr class="summary"><td colspan="2">Unsupported summary</td></tr>',
            ),
            "SPECIAL_ROW",
        ),
        (
            f"""
            <form method="post" action="main.d2l?ou=620000">
              <table>{_item_rows("501", "One", "Body")}</table>
              <table>{_item_rows("502", "Two", "Body")}</table>
            </form>
            """,
            "ANNOUNCEMENT_TABLE_COUNT",
        ),
    ],
)
def test_parser_rejects_unsupported_structures_without_guessing(
    html: str, code: str
) -> None:
    with pytest.raises(ProviderEndpointChangedError, match=code):
        parse_announcement_list_html(html, course_id=_COURSE_ID)


def test_parser_does_not_return_first_item_when_a_later_item_is_malformed() -> None:
    private_title = "PRIVATE FICTIONAL TITLE 998877"
    html = _page(
        _item_rows("501", "Valid fictional first", "First body"),
        f"""
        <tr><th><a href="/d2l/le/news/620000/502/view?ou=620000">{private_title}</a></th>
            <td>Date</td></tr>
        <tr><td><d2l-html-block html="PRIVATE BODY"></d2l-html-block></td></tr>
        """,
    )

    with pytest.raises(ProviderEndpointChangedError) as exc_info:
        parse_announcement_list_html(html, course_id=_COURSE_ID)

    assert private_title not in str(exc_info.value)
    assert "PRIVATE BODY" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("href", "code"),
    [
        (
            "https://example.brightspace.com/d2l/le/news/620000/501/view?ou=620000",
            "DETAIL_LINK",
        ),
        ("/d2l/le/news/620000/501/view?ou=999999", "COURSE_ASSOCIATION"),
        ("/d2l/le/news/620000/501/view?ou=620000&state=private", "DETAIL_LINK_QUERY"),
    ],
)
def test_parser_rejects_unsafe_or_mismatched_detail_references(
    href: str, code: str
) -> None:
    html = _page(
        f"""
        <tr><th><a href="{href}">Title</a></th><td>Date</td></tr>
        <tr><td colspan="2"><d2l-html-block html="Body"></d2l-html-block></td></tr>
        """
    )

    with pytest.raises(ProviderEndpointChangedError, match=code):
        parse_announcement_list_html(html, course_id=_COURSE_ID)


def test_parser_ignores_decorative_nested_table_without_announcement_elements() -> None:
    html = _page(
        """
        <tr><th><a href="/d2l/le/news/620000/501/view?ou=620000">Title</a></th>
            <td>Date</td></tr>
        <tr><td colspan="2"><table><tr><td>Decorative layout</td></tr></table>
            <d2l-html-block html="Readable body"></d2l-html-block></td></tr>
        """
    )

    page = parse_announcement_list_html(html, course_id=_COURSE_ID)

    assert page.items[0].body == "Readable body"


@pytest.mark.parametrize("bad_course_id", ["", "abc", "12a", "-1", "１２３"])
def test_parser_rejects_invalid_course_context(bad_course_id: str) -> None:
    with pytest.raises(ValueError, match="course_id"):
        parse_announcement_list_html(_VALID_HTML, course_id=bad_course_id)


class _FakeClient:
    def __init__(
        self, *, html: str = _VALID_HTML, raises: Exception | None = None
    ) -> None:
        self.html = html
        self.raises = raises
        self.calls: list[tuple[str, Mapping[str, str] | None, str]] = []

    def get_text(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        expected_content_type: str = "text/html",
    ) -> D2LTextResponse:
        self.calls.append((path, params, expected_content_type))
        if self.raises is not None:
            raise self.raises
        return D2LTextResponse(200, self.html, "text/html")


def test_provider_uses_exactly_one_established_listing_get() -> None:
    client = _FakeClient()

    page = BrightspaceAnnouncementProvider(client).get_announcement_list(  # type: ignore[arg-type]
        _COURSE_ID
    )

    assert len(page.items) == 2
    assert client.calls == [(_NEWS_PATH, {"ou": _COURSE_ID}, "text/html")]
    assert all("/view" not in path for path, _params, _content_type in client.calls)


def test_provider_rejects_bad_course_id_before_request() -> None:
    client = _FakeClient()
    with pytest.raises(ValueError, match="course_id"):
        BrightspaceAnnouncementProvider(client).list_announcements("not-numeric")  # type: ignore[arg-type]
    assert client.calls == []


def test_provider_propagates_provider_failure() -> None:
    client = _FakeClient(raises=PermissionDeniedError("denied"))
    with pytest.raises(PermissionDeniedError):
        BrightspaceAnnouncementProvider(client).list_announcements(_COURSE_ID)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (
            httpx.Response(302, headers={"location": "/d2l/other/path"}),
            UnexpectedProviderError,
        ),
        (
            httpx.Response(302, headers={"location": "/d2l/login?sessionExpired=1"}),
            NotAuthenticatedError,
        ),
        (httpx.Response(403), PermissionDeniedError),
        (
            httpx.Response(200, json={"fictional": True}),
            MalformedResponseError,
        ),
    ],
)
@respx.mock
def test_real_provider_safely_propagates_http_boundaries(
    response: httpx.Response, error_type: type[Exception]
) -> None:
    route = respx.get(f"{_BASE}{_NEWS_PATH}").mock(return_value=response)

    with D2LHttpClient(_BASE) as client:
        with pytest.raises(error_type):
            BrightspaceAnnouncementProvider(client).list_announcements(_COURSE_ID)

    assert route.call_count == 1
    assert {call.request.method for call in respx.calls} == {"GET"}


@respx.mock
def test_real_provider_propagates_transport_failure() -> None:
    respx.get(f"{_BASE}{_NEWS_PATH}").mock(
        side_effect=httpx.ConnectError("fictional transport failure")
    )

    with D2LHttpClient(_BASE) as client:
        with pytest.raises(NetworkUnavailableError):
            BrightspaceAnnouncementProvider(client).list_announcements(_COURSE_ID)

    assert len(respx.calls) == 1
