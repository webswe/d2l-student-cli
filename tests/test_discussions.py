"""Discussions: forums, topics, paged posts, privacy, bounds, and the CLI.

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
    D2LError,
    NotAuthenticatedError,
    PermissionDeniedError,
    ProviderEndpointChangedError,
)
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.discussion_service import DiscussionService
from d2l_cli.models.discussion import (
    EMPTY_PAGE_OBSERVED,
    END_NOT_CONFIRMED,
    PAGE_CAP_REACHED,
    DiscussionPosts,
    ForumListing,
    TopicListing,
)
from d2l_cli.providers import brightspace_discussions as bd
from d2l_cli.providers.brightspace_discussions import (
    BrightspaceDiscussionProvider,
    parse_forums,
    parse_post,
    parse_topics,
)

BASE = "https://example.brightspace.com"
COURSE, FORUM, TOPIC = "620000", "11", "22"
VERSIONS = [{"ProductCode": "le", "SupportedVersions": ["1.74", "1.82", "1.90"]}]
ROOT = f"/d2l/api/le/1.90/{COURSE}/discussions/forums/"
POSTS_PATH = f"{ROOT}{FORUM}/topics/{TOPIC}/posts/"


def raw_post(post_id: int = 1, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ForumId": int(FORUM),
        "TopicId": int(TOPIC),
        "PostId": post_id,
        "ThreadId": post_id if extra.get("ParentPostId") is None else 1,
        "ParentPostId": None,
        "PostingUserId": 7777,
        "PostingUserDisplayName": "Alex Fictional",
        "IsAnonymous": False,
        "Subject": f"Subject {post_id}",
        "Message": {"Text": f"text {post_id}", "Html": f"<p>text {post_id}</p>"},
        "DatePosted": "2026-03-10T15:00:00.000Z",
        "LastEditedDate": None,
        "IsDeleted": False,
        "RequiresApproval": False,
        "ReplyPostIds": [],
    }
    body.update(extra)
    return body


def client() -> D2LHttpClient:
    return D2LHttpClient(BASE, auth_header_provider=lambda: {"Cookie": "session=fictional"})


def parse(node: dict[str, Any]):  # type: ignore[no-untyped-def]
    return parse_post(node, course_id=COURSE, forum_id=FORUM, topic_id=TOPIC)


# --- forums and topics ---------------------------------------------------------------------------


def test_forums_and_topics_are_normalized() -> None:
    forums = parse_forums(
        [{"ForumId": 11, "Name": "General\x1b[0m", "Description": {"Html": "<p>Hi</p>"}}],
        course_id=COURSE,
    )
    assert forums.forums[0].name == "General" and forums.forums[0].description == "Hi"
    topics = parse_topics(
        [{"TopicId": 22, "ForumId": 11, "Name": "Week 1", "Description": None}],
        course_id=COURSE,
        forum_id=FORUM,
    )
    assert topics.topics[0].topic_id == "22" and topics.topics[0].description is None


@pytest.mark.parametrize(
    "payload",
    [
        "x",
        [{"ForumId": "a", "Name": "n"}],
        [{"ForumId": 1, "Name": None}],
        [{"ForumId": 1, "Name": "a"}, {"ForumId": 1, "Name": "b"}],
        [{"ForumId": 1, "Name": "a", "Description": 5}],
    ],
)
def test_malformed_forum_responses_fail_closed(payload: object) -> None:
    with pytest.raises(ProviderEndpointChangedError, match="Discussion structure is unsupported"):
        parse_forums(payload, course_id=COURSE)


def test_a_topic_for_another_forum_is_refused() -> None:
    with pytest.raises(ProviderEndpointChangedError, match="FORUM_MISMATCH"):
        parse_topics([{"TopicId": 1, "ForumId": 99, "Name": "n"}], course_id=COURSE, forum_id=FORUM)


def test_empty_forum_and_topic_lists_are_valid_and_next_pages_are_reported() -> None:
    assert parse_forums([], course_id=COURSE).forums == ()
    listing = parse_topics({"Objects": [], "Next": "https://x"}, course_id=COURSE, forum_id=FORUM)
    assert listing.has_more and not listing.complete


# --- post parsing --------------------------------------------------------------------------------


def test_post_fields_threads_and_reply_counts() -> None:
    root = parse(raw_post(1, ReplyPostIds=[2, 3]))
    reply = parse(raw_post(2, ParentPostId=1))
    assert (root.parent_post_id, root.thread_id, root.reply_count) == (None, "1", 2)
    assert (reply.parent_post_id, reply.thread_id) == ("1", "1")
    assert root.author == "Alex Fictional" and root.body == "text 1" and root.subject == "Subject 1"


def test_an_unknown_reply_list_is_unknown_not_zero() -> None:
    node = raw_post(1)
    del node["ReplyPostIds"]
    assert parse(node).reply_count is None
    assert parse(raw_post(1, ReplyPostIds=[])).reply_count == 0


def test_anonymous_authors_are_never_shown_and_user_ids_never_appear() -> None:
    post = parse(raw_post(1, IsAnonymous=True))
    assert post.author is None and post.anonymous is True
    assert "7777" not in repr(post) and not hasattr(post, "user_id")


def test_deleted_posts_keep_their_place_but_have_no_text_or_author() -> None:
    post = parse(raw_post(1, IsDeleted=True, ReplyPostIds=[2]))
    assert post.deleted and post.body is None and post.subject is None and post.author is None
    assert post.reply_count == 1


def test_moderation_state_is_reported() -> None:
    assert parse(raw_post(1, RequiresApproval=True)).requires_approval is True


def test_bodies_are_sanitized_plain_text_and_long_bodies_are_marked_truncated() -> None:
    html = "<p>Hi <b>there</b></p><script>steal()</script><iframe src='x'></iframe>\x1b[31m"
    post = parse(raw_post(1, Message={"Html": html}, Subject="Re:\x07 hi"))
    assert post.body is not None and "<" not in post.body and "steal" not in post.body
    assert "\x1b" not in post.body and "\x07" not in (post.subject or "")
    long_post = parse(raw_post(2, Message={"Text": "x" * 30_000}))
    assert long_post.body is not None and len(long_post.body) == 20_000 and long_post.body_truncated
    assert parse(raw_post(3)).body_truncated is False


def test_a_body_too_large_to_sanitize_fails_closed() -> None:
    with pytest.raises(ProviderEndpointChangedError, match="MESSAGE"):
        parse(raw_post(1, Message={"Html": "<p>" + "x" * 300_000 + "</p>"}))


@pytest.mark.parametrize(
    "node",
    [
        raw_post(1, TopicId=999),
        raw_post(1, ForumId=999),
        raw_post(1, PostId="abc"),
        raw_post(1, ThreadId=None),
        raw_post(1, ParentPostId=1),
        raw_post(1, DatePosted="soon"),
        raw_post(1, IsDeleted="no"),
        raw_post(1, ReplyPostIds="2,3"),
        raw_post(1, ReplyPostIds=["x"]),
        raw_post(1, Message=7),
    ],
)
def test_malformed_posts_fail_closed(node: dict[str, Any]) -> None:
    with pytest.raises(ProviderEndpointChangedError, match="Discussion structure is unsupported"):
        parse(node)


# --- paging and transport ---------------------------------------------------------------------


def page_of(start: int, count: int, **extra: Any) -> list[dict[str, Any]]:
    return [raw_post(i, **extra) for i in range(start, start + count)]


def versions_route() -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(return_value=httpx.Response(200, json=VERSIONS))


def posts_route(*replies: httpx.Response) -> respx.Route:
    return respx.get(f"{BASE}{POSTS_PATH}").mock(side_effect=list(replies))


def ok(payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload)


def read() -> DiscussionPosts:
    return BrightspaceDiscussionProvider(client()).list_posts(COURSE, FORUM, TOPIC)


@respx.mock
def test_a_short_page_is_not_an_end_signal_only_an_observed_empty_page_is() -> None:
    versions_route()
    route = posts_route(ok(page_of(1, 5)), ok([]))
    result = read()
    assert len(result.posts) == 5 and result.complete
    assert result.completeness_basis == EMPTY_PAGE_OBSERVED and result.pages_fetched == 2
    assert route.call_count == 2  # the confirming request for the page after the short one
    first, second = (call.request.url.params for call in route.calls)
    assert (first["pageSize"], first["pageNumber"], second["pageNumber"]) == (
        str(bd.PAGE_SIZE),
        "1",
        "2",
    )
    assert first["sort"] == "creationdate" and "threadId" not in first


@respx.mock
def test_posts_after_a_short_page_are_still_read_because_a_short_page_proves_nothing() -> None:
    versions_route()
    route = posts_route(ok(page_of(1, 3)), ok(page_of(10, 2)), ok([]))
    result = read()
    assert [p.post_id for p in result.posts] == ["1", "2", "3", "10", "11"]
    assert result.complete and route.call_count == 3


@respx.mock
def test_an_empty_first_page_is_a_confirmed_empty_topic_not_a_failure() -> None:
    versions_route()
    result = posts_route(ok([])) and read()
    assert result.posts == () and result.complete and result.pages_fetched == 1


@respx.mock
def test_an_exact_full_page_then_an_empty_one_is_complete() -> None:
    versions_route()
    posts_route(ok(page_of(1, bd.PAGE_SIZE)), ok([]))
    result = read()
    assert result.complete and len(result.posts) == bd.PAGE_SIZE


@respx.mock
def test_reaching_the_page_cap_is_incomplete_with_the_cap_as_the_stated_basis() -> None:
    versions_route()
    pages = [ok(page_of(n * 1000, bd.PAGE_SIZE)) for n in range(bd.MAX_PAGES)]
    route = posts_route(*pages)
    result = read()
    assert route.call_count == bd.MAX_PAGES and not result.complete
    assert result.completeness_basis == PAGE_CAP_REACHED
    assert len(result.posts) == bd.MAX_PAGES * bd.PAGE_SIZE and result.pages_fetched == bd.MAX_PAGES


@respx.mock
def test_exactly_the_cap_with_an_empty_next_page_is_never_requested_so_stays_incomplete() -> None:
    versions_route()
    route = posts_route(*[ok(page_of(n * 1000, bd.PAGE_SIZE)) for n in range(bd.MAX_PAGES)], ok([]))
    result = read()
    assert route.call_count == bd.MAX_PAGES  # one page beyond the cap is never requested
    assert result.completeness_basis == PAGE_CAP_REACHED


@respx.mock
@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_a_later_page_failure_keeps_the_posts_and_says_the_end_was_not_confirmed(
    status: int,
) -> None:
    versions_route()
    posts_route(ok(page_of(1, 4)), httpx.Response(status))
    result = read()
    assert len(result.posts) == 4 and not result.complete
    assert result.completeness_basis == END_NOT_CONFIRMED and result.pages_fetched == 1


@respx.mock
@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_a_first_page_failure_is_an_error_never_an_empty_discussion(status: int) -> None:
    versions_route()
    posts_route(httpx.Response(status))
    with pytest.raises(D2LError):
        read()


@respx.mock
def test_an_expired_session_on_a_later_page_aborts_instead_of_returning_partial_data() -> None:
    versions_route()
    posts_route(ok(page_of(1, 4)), httpx.Response(401))
    with pytest.raises(NotAuthenticatedError):
        read()


@respx.mock
def test_pages_over_the_text_budget_stop_the_read_and_say_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bd, "MAX_TOTAL_TEXT_CHARS", 50)
    versions_route()
    big = [raw_post(1, Message={"Text": "y" * 200})]
    result = posts_route(ok(big), ok([])) and read()
    assert result.completeness_basis == bd.SIZE_CAP_REACHED and not result.complete


@respx.mock
def test_a_server_that_ignores_page_size_fails_closed() -> None:
    versions_route()
    posts_route(ok(page_of(1, bd.PAGE_SIZE + 1)))
    with pytest.raises(ProviderEndpointChangedError, match="ITEM_LIMIT"):
        read()


@respx.mock
@pytest.mark.parametrize(
    ("second", "code"),
    [
        (page_of(1, 2), "DUPLICATE_POST"),  # a repeated page
        (list(reversed(page_of(1, bd.PAGE_SIZE))), "DUPLICATE_POST"),  # reordered repeat
        ([raw_post(1)], "DUPLICATE_POST"),
    ],
)
def test_repeated_or_reordered_pages_fail_closed_rather_than_inflate_the_result(
    second: list[dict[str, Any]], code: str
) -> None:
    versions_route()
    posts_route(ok(page_of(1, bd.PAGE_SIZE)), ok(second))
    with pytest.raises(ProviderEndpointChangedError, match=code):
        read()


@respx.mock
@pytest.mark.parametrize(
    "payload",
    [
        {"Objects": [], "Next": "https://evil.example/x"},  # a cursor is not part of this contract
        {"Objects": "x"},
        {"Next": 5},
        "text",
        [raw_post(1), "x"],
    ],
)
def test_malformed_pagination_shapes_fail_closed_and_a_cursor_is_never_followed(
    payload: object,
) -> None:
    versions_route()
    follow = respx.get(url__regex=r".*evil.*").mock(return_value=httpx.Response(200, json=[]))
    posts_route(ok(payload))
    with pytest.raises(ProviderEndpointChangedError):
        read()
    assert follow.call_count == 0


@respx.mock
def test_a_thread_filter_is_sent_and_every_returned_post_must_belong_to_it() -> None:
    versions_route()
    route = posts_route(ok([raw_post(1), raw_post(2, ParentPostId=1)]), ok([]))
    result = BrightspaceDiscussionProvider(client()).list_posts(COURSE, FORUM, TOPIC, "1")
    assert route.calls[0].request.url.params["threadId"] == "1" and len(result.posts) == 2
    assert route.calls[1].request.url.params["threadId"] == "1"
    posts_route_wrong = respx.get(f"{BASE}{POSTS_PATH}").mock(return_value=ok([raw_post(5)]))
    assert posts_route_wrong
    with pytest.raises(ProviderEndpointChangedError, match="THREAD_MISMATCH"):
        BrightspaceDiscussionProvider(client()).list_posts(COURSE, FORUM, TOPIC, "1")


@respx.mock
@pytest.mark.parametrize("field", ["TopicId", "ForumId"])
def test_posts_from_another_topic_or_forum_fail_closed(field: str) -> None:
    versions_route()
    posts_route(ok([raw_post(1, **{field: 999})]))
    with pytest.raises(ProviderEndpointChangedError, match="MISMATCH"):
        read()


@respx.mock
def test_only_documented_get_routes_are_used_and_never_read_status_or_writes() -> None:
    versions_route()
    respx.get(f"{BASE}{ROOT}").mock(return_value=ok([{"ForumId": 11, "Name": "F"}]))
    respx.get(f"{BASE}{ROOT}{FORUM}/topics/").mock(return_value=ok([{"TopicId": 22, "Name": "T"}]))
    posts_route(ok([raw_post(1)]), ok([]))
    tripwire = respx.route(
        url__regex=r".*/(ReadStatus|Rating|Votes|Flag|Approval|attachments|access|groupRestrictions).*"
    ).mock(return_value=ok({}))
    writes = respx.route(method__in=["POST", "PUT", "PATCH", "DELETE"]).mock(
        return_value=httpx.Response(200)
    )
    provider = BrightspaceDiscussionProvider(client())
    provider.list_forums(COURSE)
    provider.list_topics(COURSE, FORUM)
    provider.list_posts(COURSE, FORUM, TOPIC)
    assert tripwire.call_count == 0 and writes.call_count == 0


@respx.mock
def test_access_denial_is_an_error_not_an_empty_discussion() -> None:
    versions_route()
    respx.get(f"{BASE}{POSTS_PATH}").mock(return_value=httpx.Response(403))
    with pytest.raises(PermissionDeniedError):
        read()


# --- text safety ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("html", "must_not_contain"),
    [
        ("<script>alert(1)</script>ok", ("<", "alert")),
        ("<style>body{x:y}</style>ok", ("<", "body{")),
        ("&lt;script&gt;alert(1)&lt;/script&gt;", ("&lt;", "&gt;")),  # decoded to inert plain text
        ("<p>unclosed <b>bold <i>italic", ("<",)),
        ("<a href='javascript:evil()'>click</a>", ("javascript:", "<")),
        ("<img src=x onerror=evil()>", ("onerror", "<")),
        ("fine\x1b[2J\x1b[Hcleared", ("\x1b",)),
        ("line\rOVERWRITE", ("\r",)),
        ("back\x08\x08\x08spoof", ("\x08",)),
        ("bell\x07 tab\there\x00 nul", ("\x07", "\x00")),
        ("\x1b]8;;https://evil.example\x07link\x1b]8;;\x07", ("\x1b", "\x07")),
        ("<iframe src='https://evil.example'></iframe>after", ("<", "iframe")),
    ],
)
def test_terminal_and_markup_attacks_are_neutralized_in_every_text_field(
    html: str, must_not_contain: tuple[str, ...]
) -> None:
    post = parse(raw_post(1, Message={"Html": html}, Subject=html[:60]))
    for forbidden in must_not_contain:
        assert forbidden not in (post.body or "")
    # The subject is a plain-text field: markup characters are inert there, controls are not.
    for control in (0x1B, 0x07, 0x00, 0x0D, 0x08):
        assert chr(control) not in (post.subject or "")


def test_urls_and_markdown_like_text_stay_plain_data() -> None:
    post = parse(raw_post(1, Message={"Text": "[click](https://evil.example) **x** `y` https://z"}))
    assert post.body is not None and "evil.example" in post.body  # shown as text, never fetched


def test_unicode_edge_cases_survive_without_control_effects() -> None:
    post = parse(raw_post(1, Message={"Text": "café ‮evil‬ ​zw \U0001f600"}))
    assert post.body is not None and "‮" not in post.body and "café" in post.body


def test_the_original_body_is_never_in_an_error_message() -> None:
    secret = "ORIGINAL_BODY_SECRET_77"
    with pytest.raises(ProviderEndpointChangedError) as raised:
        parse(raw_post(1, Message={"Html": f"<p>{secret}" + "x" * 300_000 + "</p>"}))
    assert secret not in str(raised.value)


@respx.mock
@pytest.mark.parametrize("bad", ["abc", "1/..", "", "9" * 25])
def test_invalid_ids_are_rejected_before_any_request(bad: str) -> None:
    route = respx.route().mock(return_value=httpx.Response(200, json=[]))
    provider = BrightspaceDiscussionProvider(client())
    calls = [
        lambda: provider.list_forums(bad),
        lambda: provider.list_topics(COURSE, bad),
        lambda: provider.list_posts(COURSE, FORUM, bad),
        lambda: provider.list_posts(COURSE, bad, TOPIC),
        lambda: provider.list_posts(COURSE, FORUM, TOPIC, bad),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="numeric"):
            call()
    assert route.call_count == 0


@respx.mock
def test_an_unsupported_api_version_fails_before_any_discussion_request() -> None:
    respx.get(f"{BASE}/d2l/api/versions/").mock(
        return_value=httpx.Response(
            200, json=[{"ProductCode": "le", "SupportedVersions": ["1.74"]}]
        )
    )
    route = respx.get(url__regex=r".*/discussions/.*").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(ProviderEndpointChangedError, match="VERSION_UNSUPPORTED"):
        BrightspaceDiscussionProvider(client()).list_forums(COURSE)
    assert route.call_count == 0


# --- CLI -----------------------------------------------------------------------------------------

runner = CliRunner()


class _Auth:
    def __init__(self, headers: Mapping[str, str] | Exception) -> None:
        self._headers = headers

    def header_provider(self) -> Mapping[str, str]:
        if isinstance(self._headers, Exception):
            raise self._headers
        return self._headers


class _Provider(BrightspaceDiscussionProvider):
    def __init__(self, error: Exception | None = None, *, more: bool = False) -> None:
        self._error, self._more = error, more

    def list_forums(self, course_id: str) -> ForumListing:
        if self._error:
            raise self._error
        return parse_forums([{"ForumId": 11, "Name": "General"}], course_id=course_id)

    def list_topics(self, course_id: str, forum_id: str) -> TopicListing:
        if self._error:
            raise self._error
        return parse_topics(
            [{"TopicId": 22, "Name": "Week 1"}], course_id=course_id, forum_id=forum_id
        )

    def list_posts(
        self, course_id: str, forum_id: str, topic_id: str, thread_id: str | None = None
    ) -> DiscussionPosts:
        if self._error:
            raise self._error
        posts = (
            parse(raw_post(1)),
            parse(raw_post(2, ParentPostId=1, IsAnonymous=True)),
            parse(raw_post(3, ParentPostId=99)),
            parse(raw_post(4, IsDeleted=True)),
        )
        basis = PAGE_CAP_REACHED if self._more else EMPTY_PAGE_OBSERVED
        return DiscussionPosts(course_id, forum_id, topic_id, thread_id, posts, basis, 1)


def wire(monkeypatch: pytest.MonkeyPatch, provider: _Provider, auth: object | None = None) -> None:
    monkeypatch.setattr(cli_module, "load_config", lambda: object())
    monkeypatch.setattr(
        cli_module, "_build_auth_service", lambda config: auth or _Auth({"Cookie": "x"})
    )
    monkeypatch.setattr(
        cli_module, "_build_discussion_service", lambda config, headers: DiscussionService(provider)
    )


@pytest.mark.parametrize("json_option", [[], ["--json"]])
def test_posts_require_opt_in_before_config_credentials_or_requests(
    monkeypatch: pytest.MonkeyPatch, json_option: list[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("refused post command accessed configuration, credentials or provider")

    monkeypatch.setattr(cli_module, "_load_cli_config", forbidden)
    monkeypatch.setattr(cli_module, "_build_auth_service", forbidden)
    monkeypatch.setattr(cli_module, "_build_discussion_service", forbidden)
    args = [
        "discussions", "posts", "--course", COURSE, "--forum", FORUM,
        "--topic", TOPIC, *json_option,
    ]
    for _ in range(2):  # the acknowledgement cannot carry over to another invocation
        result = runner.invoke(cli_module.app, args)
        assert result.exit_code == 1 and result.stdout == ""
        assert "--acknowledge-read-state-risk" in result.stderr
        assert "read state" in result.stderr


def test_forums_and_topics_json_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    wire(monkeypatch, _Provider())
    forums = runner.invoke(cli_module.app, ["discussions", "forums", "--course", COURSE, "--json"])
    body = json.loads(forums.stdout)["discussion_forums"]
    assert forums.exit_code == 0 and list(body) == ["course_id", "complete", "forums"]
    assert list(body["forums"][0]) == ["forum_id", "course_id", "name", "description"]
    topics = runner.invoke(
        cli_module.app, ["discussions", "topics", "--course", COURSE, "--forum", FORUM, "--json"]
    )
    tbody = json.loads(topics.stdout)["discussion_topics"]
    assert list(tbody) == ["course_id", "forum_id", "complete", "topics"]
    assert list(tbody["topics"][0]) == ["topic_id", "forum_id", "course_id", "name", "description"]


def test_posts_json_shape_privacy_and_parent_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    wire(monkeypatch, _Provider())
    args = [
        "discussions",
        "posts",
        "--course",
        COURSE,
        "--forum",
        FORUM,
        "--topic",
        TOPIC,
        "--acknowledge-read-state-risk",
        "--json",
    ]
    result = runner.invoke(cli_module.app, args)
    assert result.exit_code == 0
    assert "read state" in result.stderr and "unverified" in result.stderr
    body = json.loads(result.stdout)["discussion_posts"]
    assert list(body) == [
        "course_id",
        "forum_id",
        "topic_id",
        "thread_id",
        "complete",
        "completeness_basis",
        "page_size",
        "pages_fetched",
        "posts",
    ]
    posts = {p["post_id"]: p for p in body["posts"]}
    assert list(posts["1"]) == [
        "post_id",
        "course_id",
        "forum_id",
        "topic_id",
        "thread_id",
        "parent_post_id",
        "parent_in_results",
        "author",
        "anonymous",
        "subject",
        "body",
        "body_truncated",
        "posted",
        "edited",
        "deleted",
        "requires_approval",
        "reply_count",
    ]
    assert posts["1"]["parent_in_results"] is None
    assert posts["2"]["parent_in_results"] is True and posts["2"]["author"] is None
    assert posts["3"]["parent_in_results"] is False  # reported as missing, not invented
    assert posts["4"]["deleted"] is True and posts["4"]["body"] is None
    assert "7777" not in result.stdout


def test_incomplete_posts_warn_on_stderr_and_stay_pure_json_on_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(monkeypatch, _Provider(more=True))
    args = [
        "discussions",
        "posts",
        "--course",
        COURSE,
        "--forum",
        FORUM,
        "--topic",
        TOPIC,
        "--acknowledge-read-state-risk",
        "--json",
    ]
    result = runner.invoke(cli_module.app, args)
    assert json.loads(result.stdout)["discussion_posts"]["complete"] is False
    assert "incomplete" in result.stderr


def test_posts_text_output_marks_replies_anonymity_and_deleted_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(monkeypatch, _Provider())
    result = runner.invoke(
        cli_module.app,
        [
            "discussions", "posts", "--course", COURSE, "--forum", FORUM,
            "--topic", TOPIC, "--acknowledge-read-state-risk",
        ],
    )
    assert "(reply to 1)" in result.stdout and "anonymous" in result.stdout
    assert "(deleted)" in result.stdout and "text 1" in result.stdout


def test_errors_exit_one_with_empty_stdout_and_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    wire(monkeypatch, _Provider(PermissionDeniedError("provider denied access to this resource")))
    for args in (
        ["forums"],
        ["topics", "--forum", FORUM],
        ["posts", "--forum", FORUM, "--topic", TOPIC, "--acknowledge-read-state-risk"],
    ):
        result = runner.invoke(cli_module.app, ["discussions", *args, "--course", COURSE, "--json"])
        assert result.exit_code == 1 and result.stdout == ""
        assert "denied" in result.stderr and "Cookie" not in result.stderr


def test_authentication_failure_and_usage_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    wire(
        monkeypatch,
        _Provider(),
        auth=_Auth(NotAuthenticatedError("No stored session; run 'd2l auth login'.")),
    )
    result = runner.invoke(cli_module.app, ["discussions", "forums", "--course", COURSE, "--json"])
    assert result.exit_code == 1 and result.stdout == "" and "auth login" in result.stderr
    missing = runner.invoke(cli_module.app, ["discussions", "posts", "--course", COURSE, "--json"])
    assert missing.exit_code == 2 and missing.stdout == ""


def test_a_service_rejects_posts_for_another_topic() -> None:
    class Wrong(_Provider):
        def list_posts(
            self, course_id: str, forum_id: str, topic_id: str, thread_id: str | None = None
        ) -> DiscussionPosts:
            return DiscussionPosts(course_id, forum_id, "999", None, (), EMPTY_PAGE_OBSERVED, 1)

    with pytest.raises(ProviderEndpointChangedError, match="mismatched"):
        DiscussionService(Wrong()).list_posts(COURSE, FORUM, TOPIC)
