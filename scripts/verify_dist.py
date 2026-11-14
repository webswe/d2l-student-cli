"""Inspect built distributions before anyone installs or publishes them.

Usage: python scripts/verify_dist.py <dist-dir>

Fails (exit 1) when the wheel or sdist is missing, carries a file that must never ship
(secrets, browser state, captures, tests, virtualenvs), lacks the console script, or has
metadata that disagrees with pyproject.toml. When pyproject.toml declares a license expression,
both artifacts must also carry the license file and the wheel metadata must state it.
Reads archives only; never extracts or runs them.
"""

from __future__ import annotations

import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

FORBIDDEN = re.compile(
    r"(^|/)(\.env|\.venv|venv|\.git|\.d2l|browser[-_]profile|playwright[-_]profile"
    r"|__pycache__|tests|scripts|tools|dist|\.mypy_cache|\.ruff_cache|\.pytest_cache)(/|$)"
    r"|\.(har|pem|key|pyc|zip|png)$"
    r"|(token|credential|session|cookie)s?[^/]*\.json$|auth_state|storage_state",
    re.IGNORECASE,
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    dist = Path(argv[1])
    meta = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    name, version = meta["name"].replace("-", "_"), meta["version"]
    problems: list[str] = []

    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        problems.append(f"expected exactly one wheel and one sdist, found {wheels} {sdists}")
    else:
        with zipfile.ZipFile(wheels[0]) as zf:
            names = zf.namelist()
            info = f"{name}-{version}.dist-info"
            for required in (f"{info}/METADATA", f"{info}/entry_points.txt", f"{name}/cli.py"):
                if required not in names:
                    problems.append(f"wheel missing {required}")
            if f"{info}/entry_points.txt" in names:
                entry = zf.read(f"{info}/entry_points.txt").decode()
                if "d2l = d2l_cli:main" not in entry:
                    problems.append("wheel console script d2l is not d2l_cli:main")
            if f"{info}/METADATA" in names:
                text = zf.read(f"{info}/METADATA").decode()
                requires = meta["requires-python"]
                for needle in (f"Version: {version}", f"Requires-Python: {requires}"):
                    if needle not in text:
                        problems.append(f"wheel METADATA lacks '{needle}'")
                expression = meta.get("license")
                if isinstance(expression, str):
                    if f"License-Expression: {expression}" not in text:
                        problems.append(f"wheel METADATA lacks License-Expression {expression}")
                    if f"{info}/licenses/LICENSE" not in names:
                        problems.append("wheel does not contain the LICENSE file")
        with tarfile.open(sdists[0]) as tf:
            sdist_names = [m.name.split("/", 1)[1] for m in tf.getmembers() if "/" in m.name]
            names += sdist_names
        if isinstance(meta.get("license"), str) and "LICENSE" not in sdist_names:
            problems.append("sdist does not contain the LICENSE file")
        problems += [f"forbidden file in distribution: {n}" for n in sorted(set(names))
                     if FORBIDDEN.search(n)]
        print(f"checked {wheels[0].name} and {sdists[0].name}: {len(names)} entries")
    for p in problems:
        print("FAIL:", p)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
