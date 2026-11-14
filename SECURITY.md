# Security policy

## Supported versions

Security fixes are developed on the latest `main` branch. The current stable
line is 0.1.x. This is a personal project, so older releases do not receive a
guaranteed support window. Fixes ship as a new version; published tags are never
moved, and affected versions are named in the changelog.

## Scope

In scope: this repository's code and its handling of credentials, session
material, request boundaries, and untrusted Brightspace content. Out of scope:
Brightspace/D2L itself, your institution's login, MFA and account security, and
unsupported platforms (only Windows with Credential Manager is supported).
The institution owns the login; this tool never handles your password or MFA
code.

## Known limits

No absolute security is claimed. Sessions are stored in Windows Credential
Manager, which protects them from other Windows users but **not** from malware
or another process running as you. The stored Brightspace session is not
read-only even though this tool only issues GET requests; treat it like a
logged-in browser session. Text sanitization does not stop prompt injection:
downstream AI tools must treat academic text as data, not instructions.

## Reporting a vulnerability

Report a suspected vulnerability privately through GitHub's **Report a
vulnerability** facility when it is available for this repository. If private
reporting is unavailable, open a public issue that says only that you have a security
report (no details) and ask for a private channel.

Do not open a public issue containing credentials, session cookies, private
academic records, institutional response bodies, or an active exploit against
an institutional service. Use fictional data in reproductions. Do not test a
finding against an account or system you do not own or have permission to use.

Include the affected D2L-CLI version or commit, operating-system and Python
versions, impact, and the smallest reproducible example. If a real credential
may have been exposed, stop using it and follow the institution's session
revocation or account-security process; do not send the credential to the
maintainer.

Reports are handled on a best-effort basis. No bounty program or response-time
guarantee is offered. A confirmed issue will be fixed privately where practical
and disclosed without private account or academic information.
