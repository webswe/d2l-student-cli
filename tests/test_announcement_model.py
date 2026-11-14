from __future__ import annotations

from d2l_cli.models.announcement import (
    Announcement,
    AnnouncementCompleteness,
    AnnouncementListPage,
)


def test_announcement_contains_only_supported_read_only_fields() -> None:
    item = Announcement(
        course_id="620000",
        announcement_id="501",
        title="Fictional Campus Update",
        body="Readable fictional body.",
        detail_reference="/d2l/le/news/620000/501/view",
    )

    assert item.body == "Readable fictional body."
    assert not hasattr(item, "raw_html")
    assert not hasattr(item, "published_at")
    assert not hasattr(item, "updated_at")
    assert not hasattr(item, "is_read")
    assert not hasattr(item, "is_urgent")
    assert not hasattr(item, "is_pinned")


def test_announcement_page_completeness_is_only_unknown() -> None:
    page = AnnouncementListPage(())

    assert page.completeness is AnnouncementCompleteness.UNKNOWN
    assert list(AnnouncementCompleteness) == [AnnouncementCompleteness.UNKNOWN]
