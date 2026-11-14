from __future__ import annotations

import pytest

from d2l_cli.announcement_service import AnnouncementService
from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.models.announcement import (
    Announcement,
    AnnouncementCompleteness,
    AnnouncementListPage,
)

_ITEM = Announcement(
    course_id="620000",
    announcement_id="501",
    title="Fictional Update",
    body=None,
    detail_reference="/d2l/le/news/620000/501/view",
)


class _FakeProvider:
    def __init__(
        self,
        *,
        page: AnnouncementListPage | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.page = page if page is not None else AnnouncementListPage((_ITEM,))
        self.raises = raises
        self.requested_course_ids: list[str] = []

    def get_announcement_list(self, course_id: str) -> AnnouncementListPage:
        self.requested_course_ids.append(course_id)
        if self.raises is not None:
            raise self.raises
        return self.page


def test_service_returns_items_in_provider_order_with_unknown_completeness() -> None:
    second = Announcement(
        course_id="620000",
        announcement_id="502",
        title="Second fictional update",
        body="Body",
        detail_reference="/d2l/le/news/620000/502/view",
    )
    provider = _FakeProvider(page=AnnouncementListPage((_ITEM, second)))
    service = AnnouncementService(provider)  # type: ignore[arg-type]

    assert service.list_announcements("620000") == [_ITEM, second]
    assert provider.requested_course_ids == ["620000"]
    assert service.get_announcement_list("620000").completeness is AnnouncementCompleteness.UNKNOWN


def test_service_handles_empty_page() -> None:
    page = AnnouncementService(  # type: ignore[arg-type]
        _FakeProvider(page=AnnouncementListPage(()))
    ).get_announcement_list("620000")

    assert page.items == ()


def test_service_rejects_mismatched_course_association_without_exposing_values() -> None:
    mismatched = Announcement(
        course_id="999999",
        announcement_id="PRIVATE-501",
        title="PRIVATE FICTIONAL TITLE",
        body="PRIVATE FICTIONAL BODY",
        detail_reference="/private/reference",
    )
    service = AnnouncementService(  # type: ignore[arg-type]
        _FakeProvider(page=AnnouncementListPage((mismatched,)))
    )

    with pytest.raises(ProviderEndpointChangedError) as exc_info:
        service.get_announcement_list("620000")

    message = str(exc_info.value)
    assert "999999" not in message
    assert "PRIVATE" not in message


def test_service_propagates_provider_errors() -> None:
    service = AnnouncementService(  # type: ignore[arg-type]
        _FakeProvider(raises=ProviderEndpointChangedError("fixed provider failure"))
    )
    with pytest.raises(ProviderEndpointChangedError, match="fixed provider failure"):
        service.get_announcement_list("620000")
