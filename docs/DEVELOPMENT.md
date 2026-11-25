# Development

## Setup (Windows, PowerShell)

```powershell
git clone https://github.com/webswe/d2l-student-cli.git
cd d2l-student-cli
uv sync --locked
uv run d2l --version
```

Prerequisites: Windows 10/11, Python 3.11+ (uv can fetch one), `uv`, Git. The Chromium browser is
needed only for real logins (`uv run playwright install chromium`), never for the tests.

## The quality gate (run before every PR)

For the local-only diagnostics command, run
`uv run pytest -q tests/test_diagnostics.py` before the full suite. The test
uses a malformed fictional URL and checks that no origin or secret text is
echoed. Diagnostics must never instantiate `AuthService`, inspect keyring, or
make a provider request. Package smoke: `uv run d2l diagnostics --json`.

```powershell
uv lock --check
uv run ruff check .
uv run mypy
uv run pytest -q
git diff --check
```

`ruff format` is not enforced: the existing code predates it and reformatting would create noise.

To check packaging as CI does:

```powershell
uv build --out-dir dist-check
uv run --no-project python scripts/verify_dist.py dist-check
```

## Where things live

See [ARCHITECTURE.md](ARCHITECTURE.md). Brightspace routes and parsing: `src/d2l_cli/providers/`.
Models and JSON shape: `src/d2l_cli/models/`. Commands: `src/d2l_cli/cli.py`.

Run one test: `uv run pytest tests/test_grade_cli.py -k json -q`.

## Fixtures and privacy

All tests use fictional data and mocked HTTP (`respx`) and a fake credential store. Never commit
real course names, grades, announcement text, cookies, HAR files or screenshots; `.gitignore`
blocks common capture formats, but you are responsible for what you add. Tests must not read the
real Credential Manager or contact Brightspace.

## Updating dependencies

Updates are deliberate and narrow: change one package, run `uv lock --upgrade-package <name>`,
re-run the gate, and review the lockfile diff. Security floors needed by wheel users (for example
`certifi>=2026.10.19`) belong in `pyproject.toml`, not only `uv.lock`, because an installed wheel
resolves the constraints rather than the lock.

Audit the locked runtime set:

```powershell
uv export --frozen --no-dev --no-emit-project --no-hashes -o runtime-requirements.txt
uvx pip-audit -r runtime-requirements.txt --no-deps --disable-pip
```

