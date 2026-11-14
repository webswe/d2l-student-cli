"""A string wrapper that never reveals its value via str()/repr()/logging.

Used everywhere a cookie/session value flows through the codebase so that
an accidental ``print(x)``, f-string, or exception message can't leak it --
the caller must explicitly call ``.reveal()`` to get the real value.
"""

from __future__ import annotations


class SecretStr:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Return the real value. Callers must not print/log the result."""
        return self._value

    def __repr__(self) -> str:
        return "SecretStr('<REDACTED>')"

    def __str__(self) -> str:
        return "<REDACTED>"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretStr):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)
