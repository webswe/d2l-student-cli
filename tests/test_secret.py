from __future__ import annotations

from d2l_cli.secret import SecretStr


def test_reveal_returns_original_value() -> None:
    assert SecretStr("super-secret").reveal() == "super-secret"


def test_repr_never_contains_value() -> None:
    assert "super-secret" not in repr(SecretStr("super-secret"))


def test_str_never_contains_value() -> None:
    assert "super-secret" not in str(SecretStr("super-secret"))


def test_fstring_never_contains_value() -> None:
    secret = SecretStr("super-secret")
    assert "super-secret" not in f"{secret}"
    assert "super-secret" not in f"{secret!r}"


def test_equality_compares_underlying_value() -> None:
    assert SecretStr("a") == SecretStr("a")
    assert SecretStr("a") != SecretStr("b")
    assert SecretStr("a") != "a"


def test_hashable() -> None:
    assert hash(SecretStr("a")) == hash(SecretStr("a"))
