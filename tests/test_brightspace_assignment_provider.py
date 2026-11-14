from __future__ import annotations

from collections.abc import Mapping

import pytest

from d2l_cli.client.errors import (
    NotAuthenticatedError,
    PermissionDeniedError,
    ProviderEndpointChangedError,
)
from d2l_cli.client.http import D2LTextResponse
from d2l_cli.providers.brightspace_assignments import BrightspaceAssignmentProvider

# Matches the Course vertical's test convention (see
# tests/test_brightspace_provider.py): the expected path is asserted as a
# literal, not imported from the module under test.
_FOLDERS_LIST_PATH = "/d2l/lms/dropbox/user/folders_list.d2l"

# Structural shape mirrors Phase 2A's accepted live evidence (see
# docs/PROVIDER_NOTES.md, "Phase 2A -- Assignment Reconnaissance"). Every id/name
# value below is synthetic.
_ASSIGNMENT_LIST_HTML = """
<d2l-table-wrapper>
  <table>
    <tr><th>Assignment</th><th>Status</th><th>Score</th><th>&nbsp;</th></tr>
    <tr>
      <td><a href="/d2l/lms/dropbox/user/folder_submit_files.d2l?db=90210&ou=620000">
        Homework 1</a></td>
      <td>Submitted</td>
      <td>18 / 20</td>
      <td></td>
    </tr>
  </table>
</d2l-table-wrapper>
"""


class FakeD2LHttpClient:
    def __init__(
        self,
        *,
        text: str = _ASSIGNMENT_LIST_HTML,
        status_code: int = 200,
        content_type: str = "text/html",
        raises: Exception | None = None,
    ) -> None:
        self._text = text
        self._status_code = status_code
        self._content_type = content_type
        self._raises = raises
        self.calls: list[tuple[str, Mapping[str, str] | None, str]] = []

    def get_text(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        expected_content_type: str = "text/html",
    ) -> D2LTextResponse:
        self.calls.append((path, params, expected_content_type))
        if self._raises is not None:
            raise self._raises
        return D2LTextResponse(
            status_code=self._status_code, text=self._text, content_type=self._content_type
        )


def test_list_assignments_parses_observed_shape() -> None:
    client = FakeD2LHttpClient()
    assignments = BrightspaceAssignmentProvider(client).list_assignments(  # type: ignore[arg-type]
        "620000"
    )
    assert len(assignments) == 1
    assert assignments[0].id == "90210"
    assert assignments[0].course_id == "620000"
    assert assignments[0].name == "Homework 1"
    assert assignments[0].submission_status == "Submitted"
    assert assignments[0].score == "18 / 20"


def test_list_assignments_requests_only_the_proven_endpoint_with_course_context() -> None:
    client = FakeD2LHttpClient()
    BrightspaceAssignmentProvider(client).list_assignments("620000")  # type: ignore[arg-type]
    assert client.calls == [(_FOLDERS_LIST_PATH, {"ou": "620000"}, "text/html")]


def test_list_assignments_never_requests_mutation_routes() -> None:
    client = FakeD2LHttpClient()
    BrightspaceAssignmentProvider(client).list_assignments("620000")  # type: ignore[arg-type]
    requested_paths = {path for path, _params, _ct in client.calls}
    assert requested_paths == {_FOLDERS_LIST_PATH}
    assert "folder_submit_files.d2l" not in requested_paths
    assert "folder_user_view_feedback.d2l" not in requested_paths


@pytest.mark.parametrize(
    "bad_course_id",
    ["", "not-a-number", "12a", " ", "-1", "\u0661\u0662\u0663", "\uff11\uff12\uff13"],
)
def test_list_assignments_rejects_non_numeric_course_id_before_requesting(
    bad_course_id: str,
) -> None:
    client = FakeD2LHttpClient()
    with pytest.raises(ValueError):
        BrightspaceAssignmentProvider(client).list_assignments(  # type: ignore[arg-type]
            bad_course_id
        )
    assert client.calls == []  # rejected before any request was built


def test_list_assignments_returns_empty_tuple_for_a_genuinely_empty_course() -> None:
    html = "<d2l-table-wrapper><table><tr><td>no assignments</td></tr></table></d2l-table-wrapper>"
    client = FakeD2LHttpClient(text=html)
    assignments = BrightspaceAssignmentProvider(client).list_assignments(  # type: ignore[arg-type]
        "1"
    )
    assert assignments == ()


def test_list_assignments_raises_on_structural_drift_not_empty_list() -> None:
    # A missing d2l-table-wrapper structure is a provider-contract failure,
    # never silently reported as "0 assignments" (see module docstring).
    client = FakeD2LHttpClient(text="<html><body>totally different page</body></html>")
    with pytest.raises(ProviderEndpointChangedError):
        BrightspaceAssignmentProvider(client).list_assignments("1")  # type: ignore[arg-type]


def test_list_assignments_propagates_not_authenticated() -> None:
    client = FakeD2LHttpClient(raises=NotAuthenticatedError("no session"))
    with pytest.raises(NotAuthenticatedError):
        BrightspaceAssignmentProvider(client).list_assignments("1")  # type: ignore[arg-type]


def test_list_assignments_propagates_permission_denied() -> None:
    client = FakeD2LHttpClient(raises=PermissionDeniedError("denied"))
    with pytest.raises(PermissionDeniedError):
        BrightspaceAssignmentProvider(client).list_assignments("1")  # type: ignore[arg-type]
