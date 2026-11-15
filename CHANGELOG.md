# Changelog

All notable user-visible changes are recorded here. The project follows the version policy in
[docs/RELEASING.md](docs/RELEASING.md). No version has been published yet.

## 0.1.0 (release date pending)

First public version.

- Read-only commands for courses, assignments (list and cross-course overview), visible grade
  items, announcements, a combined academic overview, and syllabus discovery and retrieval, each
  with a documented JSON mode (see [docs/MACHINE_INTERFACE.md](docs/MACHINE_INTERFACE.md)).
- Manual browser SSO and MFA login; the resulting session is stored only in Windows Credential
  Manager and is never written to a file. The stored session is bound to the origin it was
  created for and is refused for any other configured site.
- GET-only, HTTPS-only, exact-origin transport with bounded responses and privacy-safe errors.
- `d2l --version`.
- Offline test suite on fictional fixtures, strict type checking, reproducible builds, and a
  Windows CI matrix for Python 3.11, 3.12 and 3.13.
- Released under the MIT License.

Known limitations:

- Windows only. Kennesaw State University's Brightspace is the only deployment validated; other
  institutions are untested.
- Installation is from source with `uv`. There is no PyPI package, installer or release asset.
- Syllabus retrieval (PDF, `.docx`, plain text) is covered by offline tests only and has not been
  independently confirmed against a live institution.
- Assignment lists may be incomplete for a course that exceeds one server page; grade and
  announcement lists report completeness as `unknown`.
- There is no `logout` command; remove the stored session in Windows Credential Manager.
