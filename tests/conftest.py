"""Shared test configuration.

Typer decides at import time whether to force terminal styling: ``GITHUB_ACTIONS``,
``FORCE_COLOR`` or ``PY_COLORS`` make its Rich console emit ANSI bold/colour sequences even for
captured, non-terminal output (``NO_COLOR`` does not remove bold, and ``CI`` has no effect). Tests
that assert on human-readable help and usage text must therefore not depend on the ambient
environment.

The fixture below makes that choice explicit for every test: Typer's module-level
``FORCE_TERMINAL`` is overridden to ``False`` for the duration of each test. The environment is
left untouched (``GITHUB_ACTIONS`` stays set when it is set), the CLI code is unchanged, and
nothing else is normalized: JSON output, error text and the terminal-sanitization tests still see
exactly what the CLI wrote. A test that wants styled output opts in with
``monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", True)``; see ``test_cli_output_environment.py``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from typer import rich_utils


@pytest.fixture(autouse=True)
def _plain_cli_output(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", False)
    yield
