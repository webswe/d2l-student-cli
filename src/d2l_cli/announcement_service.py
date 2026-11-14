"""Application service for read-only visible Brightspace announcements."""

from __future__ import annotations

from d2l_cli.client.errors import ProviderEndpointChangedError
from d2l_cli.models.announcement import Announcement, AnnouncementListPage
from d2l_cli.providers.brightspace_announcements import BrightspaceAnnouncementProvider


class AnnouncementService:
    def __init__(self, provider: BrightspaceAnnouncementProvider) -> None:
        self._provider = provider

    def list_announcements(self, course_id: str) -> list[Announcement]:
        return list(self.get_announcement_list(course_id).items)

    def get_announcement_list(self, course_id: str) -> AnnouncementListPage:
        page = self._provider.get_announcement_list(course_id)
        if any(item.course_id != course_id for item in page.items):
            raise ProviderEndpointChangedError(
                "Announcements service rejected mismatched course association"
            )
        return page
