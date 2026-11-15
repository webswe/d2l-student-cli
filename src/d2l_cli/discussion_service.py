"""Application service: discussion forums, topics and posts (read-only).

The service never reads, changes or infers read state, ratings or votes, and it never marks a
page of posts as the whole discussion: an incomplete read is reported as incomplete.
"""

from __future__ import annotations

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.models.discussion import DiscussionPosts, ForumListing, TopicListing
from d2l_cli.providers.brightspace_discussions import BrightspaceDiscussionProvider

_MISMATCH = "Discussion service rejected mismatched {what} association"


class DiscussionService:
    def __init__(self, provider: BrightspaceDiscussionProvider) -> None:
        self._provider = provider

    def list_forums(self, course_id: str) -> ForumListing:
        listing = self._provider.list_forums(course_id)
        if listing.course_id != course_id or any(f.course_id != course_id for f in listing.forums):
            raise ProviderEndpointChangedError(_MISMATCH.format(what="course"))
        return listing

    def list_topics(self, course_id: str, forum_id: str) -> TopicListing:
        listing = self._provider.list_topics(course_id, forum_id)
        if (
            listing.course_id != course_id
            or listing.forum_id != forum_id
            or any(t.forum_id != forum_id for t in listing.topics)
        ):
            raise ProviderEndpointChangedError(_MISMATCH.format(what="forum"))
        return listing

    def list_posts(
        self, course_id: str, forum_id: str, topic_id: str, thread_id: str | None = None
    ) -> DiscussionPosts:
        result = self._provider.list_posts(course_id, forum_id, topic_id, thread_id)
        if (
            result.course_id != course_id
            or result.forum_id != forum_id
            or result.topic_id != topic_id
            or any(p.topic_id != topic_id for p in result.posts)
        ):
            raise ProviderEndpointChangedError(_MISMATCH.format(what="topic"))
        return result
