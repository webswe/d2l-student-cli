"""CLI human-output tests must be deterministic across CI and local environments.

Regression for hosted-CI failures: on GitHub Actions, Typer forces terminal styling, so help and
usage text contained ANSI sequences and plain-text assertions failed. These tests use only
fictional data, mocked boundaries and a null credential backend.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer import rich_utils
from typer.testing import CliRunner

from d2l_cli.announcement_sanitizer import sanitize_plain_text
from d2l_cli.cli import app

ROOT = Path(__file__).resolve().parents[1]
ESC = "\x1b"
_STYLE_VARS = ("GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS", "NO_COLOR", "CI", "CLICOLOR_FORCE")
_NESTED = "D2L_TEST_NESTED_ENVIRONMENT_RUN"
# The files that failed on the hosted runner.
_PREVIOUSLY_FAILING = (
    "tests/test_academic_overview_cli.py",
    "tests/test_announcement_cli.py",
    "tests/test_cli.py",
    "tests/test_grade_cli.py",
    "tests/test_machine_interface_contract.py",
)


def _probe_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _STYLE_VARS}
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    env.update(extra)
    return env


_PROBE = (
    "from typer.testing import CliRunner\n"
    "from d2l_cli.cli import app\n"
    "r = CliRunner().invoke(app, ['grades', 'list', '--help'])\n"
    "u = CliRunner().invoke(app, ['grades', 'list'])\n"
    "print('styled=' + str('\\x1b' in r.stdout + (u.stderr or '') + u.stdout))\n"
    "print('exit=' + str(r.exit_code) + ',' + str(u.exit_code))\n"
)


def _fresh_process_styled(**extra: str) -> bool:
    proc = subprocess.run(  # noqa: S603 - fixed interpreter and probe
        [sys.executable, "-c", _PROBE], env=_probe_env(**extra), capture_output=True, text=True,
        check=False, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr[-300:]
    assert "exit=0,2" in proc.stdout
    return "styled=True" in proc.stdout


# --- the framework's behavior, observed in fresh processes (documents the root cause) ----------


@pytest.mark.parametrize("variable", ["GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS"])
def test_framework_styles_captured_output_when_these_variables_are_set(variable: str) -> None:
    assert _fresh_process_styled(**{variable: "1"}) is True


@pytest.mark.parametrize("variable", ["CI", "CLICOLOR_FORCE"])
def test_other_color_variables_do_not_force_styling(variable: str) -> None:
    assert _fresh_process_styled(**{variable: "1"}) is False


def test_default_environment_is_plain() -> None:
    assert _fresh_process_styled() is False


def test_no_color_alone_does_not_remove_forced_styling() -> None:
    # Why NO_COLOR is not the fix: bold sequences survive it under GITHUB_ACTIONS.
    assert _fresh_process_styled(GITHUB_ACTIONS="true", NO_COLOR="1") is True


def test_the_frameworks_own_disable_switch_takes_precedence() -> None:
    assert _fresh_process_styled(GITHUB_ACTIONS="true", _TYPER_FORCE_DISABLE_TERMINAL="1") is False


# --- the test-suite policy -------------------------------------------------------------------


def test_suite_default_is_plain_even_with_a_github_actions_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert os.environ["GITHUB_ACTIONS"] == "true"  # the environment is not normalized away
    help_result = CliRunner().invoke(app, ["grades", "list", "--help"])
    usage_result = CliRunner().invoke(app, ["grades", "list"])
    assert help_result.exit_code == 0
    assert "--course" in help_result.stdout
    assert ESC not in help_result.stdout
    assert usage_result.exit_code == 2
    assert usage_result.stdout == ""
    assert "--course" in usage_result.stderr
    assert ESC not in usage_result.stderr


def test_styling_is_still_testable_when_explicitly_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", True)
    help_result = CliRunner().invoke(app, ["grades", "list", "--help"])
    usage_result = CliRunner().invoke(app, ["grades", "list"])
    assert ESC in help_result.stdout
    assert ESC in usage_result.stderr
    assert help_result.exit_code == 0
    assert usage_result.exit_code == 2
    assert usage_result.stdout == ""  # usage errors never touch stdout, styled or not


def test_version_output_is_identical_with_and_without_styling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = CliRunner().invoke(app, ["--version"])
    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", True)
    styled = CliRunner().invoke(app, ["--version"])
    assert plain.exit_code == styled.exit_code == 0
    assert plain.stdout == styled.stdout
    assert plain.stdout.startswith("d2l-cli ")
    assert ESC not in plain.stdout


def test_terminal_sanitization_is_not_weakened_by_the_plain_output_policy() -> None:
    hostile = "a\x1b]0;title\x07b\x1b[31mc\x9b2Jd‮e"
    cleaned = sanitize_plain_text(hostile)
    assert ESC not in cleaned
    assert "\x07" not in cleaned
    assert "\x9b" not in cleaned
    assert "‮" not in cleaned
    assert cleaned.startswith("a") and "d" in cleaned


# --- regression: the exact hosted-CI environment ----------------------------------------------


@pytest.mark.skipif(os.environ.get(_NESTED) == "1", reason="already inside the nested run")
@pytest.mark.parametrize("variables", [{"GITHUB_ACTIONS": "true"}, {"FORCE_COLOR": "1"}])
def test_previously_failing_files_pass_under_a_ci_like_environment(
    variables: dict[str, str],
) -> None:
    env = _probe_env(**variables)
    env[_NESTED] = "1"
    proc = subprocess.run(  # noqa: S603 - fixed interpreter, repository test files
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *_PREVIOUSLY_FAILING],
        env=env, capture_output=True, text=True, check=False, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout[-800:]
    assert " failed" not in proc.stdout
