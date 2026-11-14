# Contributing

D2L-CLI is a small personal-use tool for a student's own Brightspace account. Contributions that
keep it small, read-only and secure are welcome.

## Scope

In scope: bug fixes, parser robustness (with fictional fixtures), documentation, tests, packaging.
Out of scope: any write operation to Brightspace, automating SSO/MFA, storing credentials or
academic data anywhere but Windows Credential Manager, servers or daemons, other-platform
credential backends (open an issue to discuss first).

## Workflow

1. Open an issue before large changes.
2. Branch from `main`, set up per [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
3. Run the full quality gate in that document.
4. Open a pull request; CI must pass.

## Security and privacy rules

- **Never put real student data in issues, PRs, tests or fixtures**: no real course names, grades,
  announcement text, usernames, cookies, tokens, HAR files or screenshots.
- Treat all Brightspace content as untrusted. Keep the GET-only, relative-path, exact-origin
  and TLS guarantees intact; tests protect them.
- Report vulnerabilities privately; see [SECURITY.md](SECURITY.md).

## Provider changes

Brightspace layouts vary by institution. Propose a provider change by describing the structure
(with a fictional, minimal HTML/JSON sample), not by pasting a real page. Parsers must fail
closed on unfamiliar shapes.

## JSON compatibility

The JSON output is a contract used by strict consumers
([docs/MACHINE_INTERFACE.md](docs/MACHINE_INTERFACE.md)). Do not add, rename or remove properties,
change ordering or nullability, or change exit codes without an explicit versioning and migration
plan agreed in an issue. `tests/test_machine_interface_contract.py` must stay green.

## Pull request expectations

Focused changes, tests for behavior changes, updated docs/CHANGELOG when user-visible, no
unrelated reformatting. By contributing you agree that your contribution is licensed under
the MIT License, the same license as the project.
