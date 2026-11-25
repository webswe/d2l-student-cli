# Architecture

D2L-CLI is a local, single-process Python CLI. It has no server, daemon, cache, database or
plugin system, and none is planned. This document describes the code **as it is**. It was
verified against the import graph: there are no import cycles, `providers` never import `cli`,
Playwright is imported lazily inside the login function, and `keyring` is never called at import
time (enforced by `tests/test_import_safety.py`).

## System context

```
 Student --(manual SSO + MFA)--> Chromium (Playwright, isolated context)
    |                                   | session cookies
    v                                   v
  d2l CLI --> Windows Credential Manager (via keyring; any other backend is refused)
    |
    +- GET only, HTTPS only, one configured origin --> Brightspace (third-party, untrusted content)

 Optional local script or application --> runs `d2l ... --json` as a subprocess;
 it never sees credentials.
```

## Layers and dependency direction

Dependencies point downward only.

| Layer | Modules | Responsibility |
|---|---|---|
| Presentation / entry | `cli.py` (`d2l_cli:main`) | Typer commands, argument validation, text vs JSON rendering, the exit contract. |
| Services | `*_service.py`, `academic_overview_service.py`, `assignment_overview_service.py` | Compose provider calls into use cases; no HTTP or HTML knowledge. |
| Providers | `providers/brightspace_*.py` | Brightspace-specific routes, HTML/JSON parsing, identity validation. The only place layout assumptions live. |
| Models | `models/*.py` | Validated data classes; they define the JSON shape. |
| Transport | `client/http.py`, `client/redact.py`, `client/session_expiry.py` | GET-only httpx wrapper: exact origin, TLS on, no auto-redirect, size/encoding bounds, expiry detection. |
| Course resolution | `course_resolver.py` | Selects one active accessible course from the complete course-list service; no course-specific request until the caller explicitly invokes another command. |
| Content hierarchy | `content_service.py`, `providers/brightspace_content.py`, `models/content.py` | Uses the existing version and TOC route, preserving module/topic identity without file retrieval. |
| Errors | `client/errors.py` | `D2LError` hierarchy with privacy-safe messages. |
| Authentication | `auth_service.py`, `browser_auth.py`, `session_bridge.py` | Interactive login and session capture. |
| Credential storage | `credentials.py`, `session_store.py`, `secret.py` | Windows Credential Manager only; bounded session records; a secret wrapper that never prints. |
| Sanitization | `announcement_sanitizer.py`, `docx_guard.py` | Terminal-safe plain text; structural `.docx` safety check. |

Platform-specific behavior (Windows Credential Manager) is isolated in `credentials.py`;
everything above it is testable with a fake store. Parsing is testable without authentication
because providers take an already-built client and tests use `respx`-mocked HTTP.

## Primary request flow

`d2l grades list --course 123456 --json`

1. Typer validates options (missing or invalid: exit 2).
2. `cli.py` loads `Config` (the base URL) and reads the stored session through the
   credential layer. No session raises `NotAuthenticatedError`.
3. The service calls the provider with a `D2LHttpClient`.
4. The client rejects absolute and scheme-relative targets, sends one GET with the session,
   bounds the body, and maps a redirect to the login page to `AuthExpiredError`.
5. The provider parses untrusted HTML/JSON, validates numeric identities and course association,
   and returns models.
6. `cli.py` renders sanitized text, or JSON on stdout.

## Authentication lifecycle

`d2l auth login` opens an isolated Chromium context. The user completes SSO/MFA by hand. The
browser returning to the configured origin is only a *candidate* completion signal: the login
route (`/d2l/login`) on that origin does not count, and even another page does not prove
authentication. The CLI captures the origin-scoped cookies in memory, closes the browser
context, and verifies that candidate session with one read-only GET made with those cookies
(never with a previously stored session). Only if the provider accepts it is the session written
to Credential Manager; on any failure nothing is saved, no retry is made, and an existing stored
session is left untouched. The verification request is part of a real login; the offline tests
replace it with fakes, and no live login has been exercised by this change. No password or MFA
code is handled. `d2l auth status` re-verifies with a live GET; local
presence alone is not "authenticated". An expired session surfaces as `AuthExpiredError` with a
"log in again" message; the tool never logs in on its own.

## Error flow and exit contract

Every failure is a `D2LError` subclass whose message is safe to print. `cli._fail` writes
`error: <message>` to **stderr** (even with `--json`, so stdout stays empty on failure) and exits
`1`. Usage errors are exit `2` (Typer/Click); success is `0`. Nothing prints cookies, headers,
identifier-bearing URLs, or response bodies.

## Human vs. machine output

Human output is tab-separated or short text, passed through the terminal sanitizer. JSON output
is a frozen contract documented in `docs/MACHINE_INTERFACE.md` and pinned by
`tests/test_machine_interface_contract.py`. Do not add JSON properties without a versioning plan:
strict consumers reject unknown keys.

## Trust boundaries

1. User to CLI: arguments are untrusted input; course IDs are strictly numeric.
2. CLI to Credential Manager: backend allow-list; failures are fail-closed.
3. CLI to Brightspace: all content untrusted; exact origin; TLS verified.
4. CLI to a local script or application: stdout, stderr and exit code only.

## Extension points

Add a provider module, model, service and CLI command, with fictional fixtures (see
`docs/DEVELOPMENT.md`). There is deliberately no plugin mechanism.

## Key decisions

- **GET-only, relative-path-only client.** Cross-origin credential leakage is structurally
  impossible rather than policy-dependent.
- **Credential Manager only, no fallback.** A plaintext fallback would silently weaken the model.
- **No new JSON properties.** External consumers validate strictly.
- **One large `cli.py` (about 1,000 lines), deliberately kept.** It is flat (one function per
  command) with no shared mutable state beyond the Typer app. Splitting it would move code
  without reducing risk; revisit only if the command count grows substantially.

## Non-goals

HTTP server or API, daemon, background sync, caching or persistence of academic data, writing to
Brightspace, automating SSO/MFA, a multi-institution abstraction layer, a plugin system.
