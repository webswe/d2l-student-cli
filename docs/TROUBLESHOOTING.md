# Troubleshooting

Diagnostics intentionally omit authenticated URLs, identifiers, response bodies, credentials and
academic content. When you report a problem, include the `error:` line and `d2l --version`, never
page contents, cookies or screenshots.

| Symptom | What it means | Action |
|---|---|---|
| `d2l` does not launch or a module is missing | The environment is not synced. | From the repository root run `uv sync --locked`, then `uv run d2l --help`. |
| Playwright reports that the Chromium executable is missing | The browser is a separate download. | Run `uv run playwright install chromium`. |
| "no stored session", "session has expired", or an unexpected login redirect | There is no valid saved session. | Run `uv run d2l auth login`, complete SSO/MFA by hand, then `uv run d2l auth status`. |
| `no trusted OS-backed credential store is available` | Windows Credential Manager is not the active keyring backend. | Run on Windows with Credential Manager available. The CLI deliberately refuses plaintext or third-party fallback keyrings. |
| The base URL is rejected | Only a plain HTTPS origin is accepted. | Use for example `https://learn.example.edu`; remove paths, query strings, fragments and credentials. |
| A course identifier is rejected | Names and codes are not identifiers. | Use the numeric `id` from `d2l courses list --json`. |
| A provider-structure or content-type error | The institution's pages differ from what the parser expects. | Confirm the session with `auth status`. These errors fail closed; they are not empty results. Report the error code only. |
| An empty list is returned successfully | The provider returned no supported records for that scope. | This differs from a retrieval failure. Read the completeness warning or JSON metadata before assuming the list is exhaustive. |
| `syllabus fetch` reports an oversized or unsafe document | Files larger than 8 MiB, or documents failing the structural safety check, are refused. | Download the file from Brightspace in your browser instead. |

If a fixed error persists after a fresh login, the institution's Brightspace deployment probably
differs from the one this project was validated against.
