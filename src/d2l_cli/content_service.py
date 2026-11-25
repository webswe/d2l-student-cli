"""Course content service over the existing GET-only provider."""

from d2l_cli.models.content import ContentTree
from d2l_cli.providers.brightspace_content import BrightspaceContentProvider


class ContentService:
    def __init__(self, provider: BrightspaceContentProvider) -> None:
        self._provider = provider

    def tree(self, course_id: str) -> ContentTree:
        return self._provider.list_tree(course_id)
