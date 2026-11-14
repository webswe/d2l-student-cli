# Release process

This project uses Python package metadata and Git tags for different but
coordinated purposes. Releases are owner-approved, local-first operations; no
automation may push, publish, or create a final tag without explicit approval.

## Version policy

- `pyproject.toml` is the package-version source of truth. Regenerate and check
  `uv.lock` whenever that value changes.
- Final releases use PEP 440 `X.Y.Z` package metadata and a `vX.Y.Z` Git tag.
- A pre-release that will be built or shared as a package must use PEP 440
  metadata such as `0.2.0rc1` before building. Its Git tag may use the readable
  form `v0.2.0-rc.1`, but the tag and package metadata must identify the same
  release stage.
- Never upload two different artifacts with the same package version. Never
  move or rewrite a published tag; correct a bad release with a new version.
- Keep `CHANGELOG.md` under an `X.Y.Z (release date pending)` heading until the maintainer
  approves the final release, then replace `(release date pending)` with the date, giving
  `X.Y.Z (YYYY-MM-DD)`.

The first public version is `0.1.0`. After acceptance it receives an annotated `v0.1.0` tag on
the final release commit, created only after the commit that dates the changelog has passed CI.
A published tag is never moved. For later cycles, set pre-release package metadata before
creating distributable RC artifacts.

## Offline acceptance checklist

Run from a clean checkout with Python 3.11 and the locked environment:

```powershell
uv sync --locked
uv lock --check
uv run pytest -q
uv run ruff check .
uv run mypy --strict src
uv run d2l --help
uv build
git diff --check
git status --short --branch
```

Also scan the exact frozen runtime dependency export with a reputable Python
advisory scanner. Review every result against the upstream advisory and
application reachability; a newer package version by itself is not evidence of
a vulnerability. If remediation is needed, constrain the affected runtime
dependency in `pyproject.toml`, update only that package in `uv.lock`, and
repeat the complete acceptance checklist. `uv sync --locked` uses the exact
hashed lock, while an installed wheel resolves the compatible constraints in
its metadata; security floors needed by wheel users therefore belong in
`pyproject.toml`, not only in the lockfile.

Build into a temporary directory when verifying an uncommitted candidate so
artifacts do not enter the repository. Inspect the wheel's `console_scripts`
metadata, install it into a new temporary virtual environment, and run the
installed `d2l --help`. Tests and smoke commands must not make live Brightspace
requests. Live acceptance, when explicitly authorized, is a separate bounded
step and must never print or persist academic data or credentials.

Before tagging, verify all of the following:

1. `CHANGELOG.md` describes the release and carries the approved date.
2. Package and lockfile versions match the intended final or pre-release stage.
3. The full offline acceptance checklist passes.
4. The intended commit is reviewed and the working tree contains no unrelated
   staged or modified files.
5. The owner explicitly approves the final commit, tag, and any push or package
   publication as separate actions.

## Release blockers vs. recommendations

Blockers (do not release): failing required tests or CI on the release commit; a known
exploitable high-severity security issue; broken authentication; broken packaging or
`scripts/verify_dist.py` failure; any secret, academic record or owner path in a tracked file or
artifact; a changed JSON contract; platform or institution claims the evidence does not
support; a missing or inconsistent LICENSE; failed fresh-clone validation; unverified release artifacts.
Recommendations (do not block): additional platforms, extra badges, further refactoring.

## Maintainer release checklist

1. Confirm CI is green on the exact commit (`gh run list --branch main`).
2. Bump `version` in `pyproject.toml`, run `uv lock`, replace `(release date pending)` in
   `CHANGELOG.md` with the release date, commit.
3. In a fresh clone in a temp directory: `uv sync --locked`, the offline checklist above, then
   `uv build --out-dir <tmp>` and `python scripts/verify_dist.py <tmp>`.
4. Install the wheel into a new venv; run `d2l --version` and `d2l --help`.
5. Record SHA-256 checksums of the artifacts (`Get-FileHash`) in the release notes.
6. With owner approval: create an annotated tag `vX.Y.Z` on the verified commit and push it.
7. With separate owner approval: create the GitHub Release with the verified artifacts.

## Distribution channels (separate decisions)

- **GitHub source**: public visibility, license and protections are owner decisions.
- **GitHub Releases**: only artifacts built and inspected per the checklist above.
- **PyPI**: not published. Before the first upload, check name availability for `d2l-cli`, resolve
  the license, and prefer Trusted Publishing (OIDC) from a workflow that runs only on a manually
  approved release in a protected `pypi` environment, never on an ordinary push or tag. Validate
  on TestPyPI first as its own approved step. No publishing workflow exists in this repository.
- **Standalone Windows executable**: not available.
