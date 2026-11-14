"""Importing the package must not touch the network, the credential store, or a browser."""

from __future__ import annotations

import subprocess
import sys

_PROBE = """
import socket, sys
def _blocked(*a, **k):
    raise RuntimeError("network access at import time")
socket.socket.connect = _blocked
socket.getaddrinfo = _blocked
import keyring
def _no_store(*a, **k):
    raise RuntimeError("credential store access at import time")
for name in ("get_password", "set_password", "delete_password", "get_credential"):
    setattr(keyring, name, _no_store)
import d2l_cli, d2l_cli.cli
assert "playwright" not in sys.modules, "browser runtime imported eagerly"
print("ok")
"""


def test_import_has_no_side_effects() -> None:
    result = subprocess.run(  # noqa: S603 - fixed interpreter and fixed probe
        [sys.executable, "-I", "-c", _PROBE], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
