"""When a license is declared, the file and the metadata must agree (no-op without a license)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

pytestmark = pytest.mark.skipif("license" not in PROJECT, reason="no license declared")


def test_license_file_is_declared_and_present() -> None:
    assert PROJECT["license"] == "MIT"
    assert PROJECT["license-files"] == ["LICENSE"]
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License\n")
    assert "Copyright (c) 2026 webswe" in text


def test_no_conflicting_license_classifier() -> None:
    assert not [c for c in PROJECT.get("classifiers", []) if c.startswith("License ::")]
