"""Normalized, read-only discussion models.

Nothing here carries a user identifier, read status, rating or vote; an anonymous post carries no
author. A deleted post keeps its place in the thread but has no subject, body or author.
"""

from __future__ import annotations

from dataclasses import dataclass

#: ``completeness_basis`` values. Only the first means the whole topic was read.
EMPTY_PAGE_OBSERVED = "empty_page_observed"
PAGE_CAP_REACHED = "page_cap_reached"
SIZE_CAP_REACHED = "size_cap_reached"
END_NOT_CONFIRMED = "end_not_confirmed"  # a later page failed or was refused: end unknown


@dataclass(frozen=True, slots=True)
class DiscussionForum:
    forum_id: str
    course_id: str
    name: str
    description: str | None


@dataclass(frozen=True, slots=True)
class DiscussionTopic:
    topic_id: str
    forum_id: str
    course_id: str
    name: str
    description: str | None


@dataclass(frozen=True, slots=True)
class DiscussionPost:
    """One post. ``None`` means the provider did not report the value."""

    post_id: str
    course_id: str
    forum_id: str
    topic_id: str
    thread_id: str
    parent_post_id: str | None
    author: str | None  # display name only; None for anonymous or deleted posts
    anonymous: bool
    subject: str | None
    body: str | None
    body_truncated: bool
    posted: str | None
    edited: str | None
    deleted: bool
    requires_approval: bool
    reply_count: int | None  # from the provider's own reply list; None when it sent none


@dataclass(frozen=True, slots=True)
class ForumListing:
    course_id: str
    forums: tuple[DiscussionForum, ...]
    has_more: bool

    @property
    def complete(self) -> bool:
        return not self.has_more


@dataclass(frozen=True, slots=True)
class TopicListing:
    course_id: str
    forum_id: str
    topics: tuple[DiscussionTopic, ...]
    has_more: bool

    @property
    def complete(self) -> bool:
        return not self.has_more


@dataclass(frozen=True, slots=True)
class DiscussionPosts:
    """The posts read, in provider order, and whether every page was read."""

    course_id: str
    forum_id: str
    topic_id: str
    thread_id: str | None
    posts: tuple[DiscussionPost, ...]
    completeness_basis: str
    pages_fetched: int
    page_size: int = 100

    @property
    def complete(self) -> bool:
        """True only when an empty page was actually observed after the last post.

        The posts route documents no total, no next-page link and no end-of-data signal, so a
        short page is not treated as the end. Every other outcome says why it is not complete.
        """
        return self.completeness_basis == EMPTY_PAGE_OBSERVED
