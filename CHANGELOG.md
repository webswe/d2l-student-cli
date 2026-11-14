# Changelog

All notable user-visible changes are recorded here. The project follows the version policy in
[docs/RELEASING.md](docs/RELEASING.md). No version has been published yet.

## 0.1.0 (release date pending)

First public version.

- Read-only commands for courses, assignments (list and cross-course overview), visible grade
  items, announcements, a combined academic overview, and syllabus discovery and retrieval, each
  with a documented JSON mode (see [docs/MACHINE_INTERFACE.md](docs/MACHINE_INTERFACE.md)).
- Manual browser SSO and MFA login; the resulting session is stored only in Windows Credential
  Manager and is never written to a file.
- GET-only, HTTPS-only, exact-origin transport with bounded responses and privacy-safe errors.
- `d2l --version`.
- Offline test suite on fictional fixtures, strict type checking, reproducible builds, and a
  Windows CI matrix for Python 3.11, 3.12 and 3.13.
- Released under the MIT License.
