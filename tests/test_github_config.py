"""Static checks of the GitHub configuration (workflow and Dependabot); no network, no secrets."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
DEPENDABOT = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")


def _code(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_workflow_token_is_read_only_and_there_are_no_secrets_or_publishing() -> None:
    code = _code(WORKFLOW)
    assert re.search(r"^permissions:\n  contents: read\n", code, re.MULTILINE)
    forbidden_words = (
        "secrets.", "id-token", "pull_request_target", "workflow_run",
        "uv publish", "twine", "gh-action-pypi-publish", "gh release",
    )
    for forbidden in forbidden_words:
        assert forbidden not in code.lower(), forbidden
    assert ": write" not in code.lower()


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    uses = re.findall(r"uses:\s*(\S+)", _code(WORKFLOW))
    assert uses
    for reference in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", reference), reference


def test_checkout_does_not_persist_credentials() -> None:
    assert _code(WORKFLOW).count("persist-credentials: false") == _code(WORKFLOW).count(
        "actions/checkout@"
    )


def test_matrix_covers_all_supported_pythons_on_windows() -> None:
    assert 'python: ["3.11", "3.12", "3.13"]' in WORKFLOW
    assert "windows-latest" in WORKFLOW


def test_dependabot_is_version_2_with_the_two_expected_ecosystems() -> None:
    code = _code(DEPENDABOT)
    assert code.startswith("version: 2\n")
    assert re.findall(r"package-ecosystem:\s*(\S+)", code) == ["github-actions", "uv"]


def test_dependabot_is_bounded_monthly_and_never_merges_automatically() -> None:
    code = _code(DEPENDABOT)
    assert re.findall(r"interval:\s*(\S+)", code) == ["monthly", "monthly"]
    limits = [int(n) for n in re.findall(r"open-pull-requests-limit:\s*(\d+)", code)]
    assert limits and max(limits) <= 2
    assert "auto-merge" not in code.lower() and "automerge" not in code.lower()


def test_dependabot_keeps_tooling_separate_and_excludes_the_build_backend() -> None:
    code = _code(DEPENDABOT)
    assert "development-tools:" in code and "runtime-dependencies:" in code
    assert 'dependency-name: "uv-build"' in code
    assert 'dependency-name: "uv_build"' in code
