"""Portable skill creation uses only fictional paths and no credential store."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import d2l_cli.agent_skill as agent_skill
from d2l_cli.agent_skill import SKILL_TEXT, generate_skill, safe_destination
from d2l_cli.cli import app


def test_explicit_generation_and_no_overwrite(tmp_path: Path) -> None:
    destination = generate_skill(Path("AGENT.md"), cwd=tmp_path)
    assert destination.read_text(encoding="utf-8") == SKILL_TEXT
    with pytest.raises(ValueError, match="already exists"):
        generate_skill(Path("AGENT.md"), cwd=tmp_path)
    assert destination.read_text(encoding="utf-8") == SKILL_TEXT


def test_path_traversal_and_outside_absolute_path_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_destination(Path("../outside.md"), cwd=tmp_path)
    with pytest.raises(ValueError):
        safe_destination(tmp_path.parent / "outside.md", cwd=tmp_path)
    with pytest.raises(ValueError):
        safe_destination(Path("missing/AGENT.md"), cwd=tmp_path)
    existing_dir = tmp_path / "existing"
    existing_dir.mkdir()
    with pytest.raises(ValueError):
        safe_destination(Path("existing/AGENT.md"), cwd=tmp_path)


def test_symlink_parent_and_existing_symlink_are_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(ValueError):
        safe_destination(Path("link/AGENT.md"), cwd=tmp_path)
    file_link = tmp_path / "AGENT.md"
    file_link.symlink_to(real / "other.md")
    with pytest.raises(ValueError):
        safe_destination(Path("AGENT.md"), cwd=tmp_path)


def test_dangling_symlink_cannot_redirect_generation(tmp_path: Path) -> None:
    target = tmp_path / "uncreated.md"
    link = tmp_path / "AGENT.md"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(ValueError, match="already exists"):
        generate_skill(Path("AGENT.md"), cwd=tmp_path)
    assert link.is_symlink()
    assert not target.exists()


def test_final_symlink_check_runs_on_platforms_without_symlink_privilege(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "AGENT.md"
    original = Path.is_symlink

    def simulated_link(path: Path) -> bool:
        return path == destination or original(path)

    monkeypatch.setattr(Path, "is_symlink", simulated_link)
    with pytest.raises(ValueError, match="already exists"):
        generate_skill(Path("AGENT.md"), cwd=tmp_path)
    assert not destination.exists()


def test_exclusive_creation_rejects_symlink_added_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "uncreated.md"
    link = tmp_path / "AGENT.md"
    original = agent_skill.safe_destination

    def race(raw: Path, *, cwd: Path) -> Path:
        checked = original(raw, cwd=cwd)
        link.symlink_to(target)
        return checked

    try:
        probe = tmp_path / "probe"
        probe.symlink_to(target)
        probe.unlink()
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    monkeypatch.setattr(agent_skill, "safe_destination", race)
    with pytest.raises(FileExistsError):
        generate_skill(Path("AGENT.md"), cwd=tmp_path)
    assert not target.exists()
    assert list(tmp_path.glob(".d2l-agent-*")) == []


def test_creation_rejects_file_added_after_validation_without_symlink_privilege(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "AGENT.md"
    original = agent_skill.safe_destination

    def race(raw: Path, *, cwd: Path) -> Path:
        checked = original(raw, cwd=cwd)
        destination.write_text("existing", encoding="utf-8")
        return checked

    monkeypatch.setattr(agent_skill, "safe_destination", race)
    with pytest.raises(FileExistsError):
        generate_skill(Path("AGENT.md"), cwd=tmp_path)
    assert destination.read_text(encoding="utf-8") == "existing"
    assert list(tmp_path.glob(".d2l-agent-*")) == []


def test_preview_has_no_write_and_requires_no_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["agent", "skill", "generate", "--preview"])
    assert result.exit_code == 0
    assert result.stdout == SKILL_TEXT
    assert list(tmp_path.iterdir()) == []


def test_cli_requires_output_and_reports_safe_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    missing = runner.invoke(app, ["agent", "skill", "generate"])
    assert missing.exit_code == 2 and missing.stdout == ""
    created = runner.invoke(app, ["agent", "skill", "generate", "--output", "AGENT.md"])
    assert created.exit_code == 0
    assert (tmp_path / "AGENT.md").read_text(encoding="utf-8") == SKILL_TEXT
    repeated = runner.invoke(app, ["agent", "skill", "generate", "--output", "AGENT.md"])
    assert repeated.exit_code == 1 and repeated.stdout == ""


def test_generated_text_contains_no_student_data_or_credentials() -> None:
    assert "schema v1" in SKILL_TEXT
    assert "SSO" in SKILL_TEXT
    assert "partial" in SKILL_TEXT
    assert "Brightspace data" in SKILL_TEXT
