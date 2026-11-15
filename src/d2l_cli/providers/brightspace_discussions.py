"""GET-only provider for discussion forums, topics and posts a student may read.

Routes (documented Valence ``le`` discussions API, LE version 1.82 or later):

* ``GET /d2l/api/le/{version}/{orgUnitId}/discussions/forums/``
* ``GET .../discussions/forums/{forumId}/topics/``
* ``GET .../discussions/forums/{forumId}/topics/{topicId}/posts/`` with ``pageSize``,
  ``pageNumber`` and an optional ``threadId``

Deliberately NOT used: the ``ReadStatus``, ``Rating``, ``Votes``, ``Flag`` and ``Approval`` post
routes and every route that creates, edits, rates or deletes. Whether a plain ``GET .../posts/``
marks posts as read is not stated in the published reference, so that side effect is unverified;
no request is made for the purpose of changing read state.

Posts are read page by page (bounded) with an explicit ascending ``sort``. The reference
documents no total, next-page link or end-of-data signal, so only an observed *empty* page ends
the list as complete; a short page does not. A later page that fails, a page cap, a size cap,
a page larger than requested or a repeated post id is handled explicitly, never as a complete
discussion. Not confirmed against a live tenant; see
docs/ACTIVITIES.md.
"""

from __future__ import annotations

from typing import Any

from d2l_cli.client.errors import AuthExpiredError, D2LError, NotAuthenticatedError
from d2l_cli.client.http import D2LHttpClient
from d2l_cli.models.discussion import (
    EMPTY_PAGE_OBSERVED,
    END_NOT_CONFIRMED,
    PAGE_CAP_REACHED,
    SIZE_CAP_REACHED,
    DiscussionForum,
    DiscussionPost,
    DiscussionPosts,
    DiscussionTopic,
    ForumListing,
    TopicListing,
)
from d2l_cli.providers import valence_common as v

_LABEL = "Discussion"
MAX_FORUMS = 500
MAX_TOPICS = 2_000
PAGE_SIZE = 100
MAX_PAGES = 20
MAX_TOTAL_TEXT_CHARS = 5_000_000
_MAX_REPLY_IDS = 100_000


def _flag(node: dict[str, Any], key: str, code: str) -> bool:
    value = node.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise v.changed(_LABEL, code)
    return value


def parse_forums(payload: object, *, course_id: str) -> ForumListing:
    items, has_next = v.object_page(payload, _LABEL, max_items=MAX_FORUMS)
    forums: list[DiscussionForum] = []
    seen: set[str] = set()
    for item in items:
        forum_id = v.id_of(item.get("ForumId"), _LABEL, "FORUM_ID")
        if forum_id in seen:
            raise v.changed(_LABEL, "DUPLICATE_FORUM")
        seen.add(forum_id)
        forums.append(
            DiscussionForum(
                forum_id=forum_id,
                course_id=course_id,
                name=v.clean_text(item.get("Name"), _LABEL, "FORUM_NAME"),
                description=v.rich_text(item.get("Description"), _LABEL, "FORUM_DESCRIPTION"),
            )
        )
    return ForumListing(course_id=course_id, forums=tuple(forums), has_more=has_next)


def parse_topics(payload: object, *, course_id: str, forum_id: str) -> TopicListing:
    items, has_next = v.object_page(payload, _LABEL, max_items=MAX_TOPICS)
    topics: list[DiscussionTopic] = []
    seen: set[str] = set()
    for item in items:
        topic_id = v.id_of(item.get("TopicId"), _LABEL, "TOPIC_ID")
        if topic_id in seen:
            raise v.changed(_LABEL, "DUPLICATE_TOPIC")
        seen.add(topic_id)
        if (
            item.get("ForumId") is not None
            and v.id_of(item["ForumId"], _LABEL, "FORUM_ID") != forum_id
        ):
            raise v.changed(_LABEL, "FORUM_MISMATCH")
        topics.append(
            DiscussionTopic(
                topic_id=topic_id,
                forum_id=forum_id,
                course_id=course_id,
                name=v.clean_text(item.get("Name"), _LABEL, "TOPIC_NAME"),
                description=v.rich_text(item.get("Description"), _LABEL, "TOPIC_DESCRIPTION"),
            )
        )
    return TopicListing(
        course_id=course_id, forum_id=forum_id, topics=tuple(topics), has_more=has_next
    )


def _reply_count(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > _MAX_REPLY_IDS:
        raise v.changed(_LABEL, "REPLY_IDS")
    for reply in value:
        v.id_of(reply, _LABEL, "REPLY_IDS")
    return len(value)


def parse_post(
    node: dict[str, Any], *, course_id: str, forum_id: str, topic_id: str
) -> DiscussionPost:
    if v.id_of(node.get("TopicId"), _LABEL, "POST_TOPIC") != topic_id:
        raise v.changed(_LABEL, "TOPIC_MISMATCH")
    if (
        node.get("ForumId") is not None
        and v.id_of(node["ForumId"], _LABEL, "POST_FORUM") != forum_id
    ):
        raise v.changed(_LABEL, "FORUM_MISMATCH")
    post_id = v.id_of(node.get("PostId"), _LABEL, "POST_ID")
    parent = node.get("ParentPostId")
    parent_id = None if parent is None else v.id_of(parent, _LABEL, "PARENT_ID")
    if parent_id == post_id:
        raise v.changed(_LABEL, "SELF_PARENT")
    thread_id = v.id_of(node.get("ThreadId"), _LABEL, "THREAD_ID")
    deleted = _flag(node, "IsDeleted", "DELETED_FLAG")
    anonymous = _flag(node, "IsAnonymous", "ANONYMOUS_FLAG")
    body, truncated = (
        (None, False) if deleted else v.rich_text_bounded((node.get("Message")), _LABEL, "MESSAGE")
    )
    author = None
    if not deleted and not anonymous:
        author = v.optional_text(node.get("PostingUserDisplayName"), _LABEL, "AUTHOR", limit=200)
    return DiscussionPost(
        post_id=post_id,
        course_id=course_id,
        forum_id=forum_id,
        topic_id=topic_id,
        thread_id=thread_id,
        parent_post_id=parent_id,
        author=author,
        anonymous=anonymous,
        subject=None
        if deleted
        else v.optional_text(node.get("Subject"), _LABEL, "SUBJECT", limit=v.MAX_TITLE_CHARS),
        body=body,
        body_truncated=truncated,
        posted=v.utc_datetime(node.get("DatePosted"), _LABEL, "POSTED"),
        edited=v.utc_datetime(node.get("LastEditedDate"), _LABEL, "EDITED"),
        deleted=deleted,
        requires_approval=_flag(node, "RequiresApproval", "APPROVAL_FLAG"),
        reply_count=_reply_count(node.get("ReplyPostIds")),
    )


def parse_post_page(
    payload: object, *, course_id: str, forum_id: str, topic_id: str, page_size: int = PAGE_SIZE
) -> list[DiscussionPost]:
    items, has_next = v.object_page(payload, _LABEL, max_items=page_size)
    if has_next:
        # The posts route is paged by number; an ObjectListPage cursor is not part of its contract.
        raise v.changed(_LABEL, "UNEXPECTED_CURSOR")
    return [
        parse_post(item, course_id=course_id, forum_id=forum_id, topic_id=topic_id)
        for item in items
    ]


class BrightspaceDiscussionProvider:
    """Read forums, topics and posts the signed-in student may see."""

    def __init__(self, client: D2LHttpClient) -> None:
        self._client = client
        self._version: str | None = None

    def api_version(self) -> str:
        if self._version is None:
            response = self._client.get(v.VERSIONS_PATH)
            self._version = v.select_le_version(response.json_body, _LABEL)
        return self._version

    def _base(self, course_id: str) -> str:
        return f"/d2l/api/le/{self.api_version()}/{course_id}/discussions/forums/"

    def list_forums(self, course_id: str) -> ForumListing:
        course = v.require_id(course_id, "course_id")
        return parse_forums(self._client.get(self._base(course)).json_body, course_id=course)

    def list_topics(self, course_id: str, forum_id: str) -> TopicListing:
        course = v.require_id(course_id, "course_id")
        forum = v.require_id(forum_id, "forum_id")
        payload = self._client.get(f"{self._base(course)}{forum}/topics/").json_body
        return parse_topics(payload, course_id=course, forum_id=forum)

    def list_posts(
        self, course_id: str, forum_id: str, topic_id: str, thread_id: str | None = None
    ) -> DiscussionPosts:
        course = v.require_id(course_id, "course_id")
        forum = v.require_id(forum_id, "forum_id")
        topic = v.require_id(topic_id, "topic_id")
        thread = None if thread_id is None else v.require_id(thread_id, "thread_id")
        path = f"{self._base(course)}{forum}/topics/{topic}/posts/"
        posts: list[DiscussionPost] = []
        seen: set[str] = set()
        text_chars = 0
        pages = 0
        basis = PAGE_CAP_REACHED
        for number in range(1, MAX_PAGES + 1):
            # An explicit ascending ``sort`` keeps pages stable while new posts arrive (the
            # documented default is newest first, which shifts every page on each new post).
            params = {"pageSize": str(PAGE_SIZE), "pageNumber": str(number), "sort": "creationdate"}
            if thread is not None:
                params["threadId"] = thread
            try:
                payload = self._client.get(path, params=params).json_body
            except (NotAuthenticatedError, AuthExpiredError):
                raise
            except D2LError:
                if number == 1:
                    raise
                basis = END_NOT_CONFIRMED  # keep what was read; the end could not be confirmed
                break
            pages = number
            page = parse_post_page(payload, course_id=course, forum_id=forum, topic_id=topic)
            if not page:
                basis = EMPTY_PAGE_OBSERVED
                break
            for post in page:
                if post.post_id in seen:
                    raise v.changed(_LABEL, "DUPLICATE_POST")
                if thread is not None and post.thread_id != thread:
                    raise v.changed(_LABEL, "THREAD_MISMATCH")
                seen.add(post.post_id)
                posts.append(post)
                text_chars += len(post.body or "") + len(post.subject or "")
            if text_chars > MAX_TOTAL_TEXT_CHARS:
                basis = SIZE_CAP_REACHED
                break
        return DiscussionPosts(
            course_id=course,
            forum_id=forum,
            topic_id=topic,
            thread_id=thread,
            posts=tuple(posts),
            completeness_basis=basis,
            pages_fetched=pages,
            page_size=PAGE_SIZE,
        )
