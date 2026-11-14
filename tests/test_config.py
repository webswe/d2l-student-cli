from __future__ import annotations

import pytest

from d2l_cli.config import DEFAULT_BASE_URL, load_config


def test_default_base_url_is_kennesaw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("D2L_CLI_BASE_URL", raising=False)
    config = load_config()
    assert config.base_url == DEFAULT_BASE_URL == "https://kennesaw.view.usg.edu/"


def test_env_var_overrides_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("D2L_CLI_BASE_URL", "https://example.brightspace.com/")
    config = load_config()
    assert config.base_url == "https://example.brightspace.com/"
