# Milestone 1.2 content TOC compatibility decision

Status: **offline verified, provider unverified**. No Brightspace tenant, account, session or
academic record was accessed for this change. This record supplements the
[provider validation plan](M1_PROVIDER_VALIDATION_PLAN.md).

## Public contract and prior limitation

The [Brightspace content reference](https://docs.valence.desire2learn.com/res/content.html)
documents `GET /d2l/api/le/{version}/{orgUnitId}/content/toc` returning a
`ToC.TableOfContents` with nested `Modules` and `Topics`. Its current route range begins at
LE 1.82; 1.75-1.81 is deprecated and 1.74 or earlier obsolete. The same reference documents
the separate topic file GET. It does not prove that any specific institution accepts this
CLI's browser session, returns the same optional fields, or exposes every course item.

Previously the shared selector stopped at LE 1.75, so a tenant advertising only 1.82 could not
list its TOC. Raising that shared ceiling would also select modern versions for syllabus file
downloads without a separate compatibility decision.

## Dependency impact

| Caller | TOC selection | File selection | User-visible consequence |
|---|---|---|---|
| `d2l content toc` / `tree` | Preferred 1.82-1.120, otherwise 1.30-1.75 | None | Same hierarchy contract; completeness remains `unknown`. |
| `d2l syllabus find` | Same TOC policy | None | `api_version` records the TOC version. |
| `d2l syllabus fetch` | Same TOC policy to verify the listed file topic | Original 1.30-1.75 only | `api_version` records the file version; modern-only tenants fail before a file GET. |
| HTTP/auth client | No change | No change | GET-only, HTTPS, same-origin, TLS and redirect restrictions remain. |

## Selection and failure policy

- Read the advertised LE `SupportedVersions` once per provider. Reject malformed version data,
  duplicate LE product entries and noncanonical or non-string versions with a fixed error.
- For TOC listing, select the highest advertised 1.82-1.120. If none exists, select the highest
  advertised 1.30-1.75. LE 1.76-1.81 is not a fallback. Versions above 1.120 are never
  selected merely because they are advertised. The 1.120 ceiling follows the bounded current
  range already used by other academic integrations in this CLI; future versions require review.
- For explicit file retrieval, independently select the highest advertised 1.30-1.75. On a
  modern-only tenant, return `CONTENT_FILE_VERSION_UNSUPPORTED` before requesting file bytes.
  No automatic fallback, cross-origin URL or modern file route is attempted.
- On that modern-only tenant, syllabus discovery can still show candidate topics, but marks them
  non-fetchable with a fixed `file_version_unsupported` reason and recommends none.
- Both selectors use advertised availability, not proof of route acceptance. An approved
  authenticated TOC request is needed to establish session acceptance at that route; a separate
  approved file-route check is needed before any modern file policy could be considered.

The TOC parser retains visible hierarchy and rejects malformed course associations, conflicting
topic identifiers, oversized IDs, cycles and unsupported structure. Hidden and broken entries
remain excluded. `IsLocked` is not treated as an invisibility flag because the public schema
does not establish that rule. Topic `Url` values are only inspected for an extension and are
never fetched. The CLI never adds `ignoreDateRestrictions` or another-user `userId` parameters.

## Evidence levels and remaining gates

| Capability | Evidence now | Required future decision |
|---|---|---|
| TOC route and broad shape | **DOCUMENTED** in the public reference; **OFFLINE VERIFIED** with fictional tests | **PROVIDER UNVERIFIED** for authenticated session, exact shape, permission and visibility. |
| TOC completeness | Parser reports `unknown`; dates, release rules and permissions can omit items | **PROVIDER UNVERIFIED**; an empty TOC cannot establish no content. |
| Legacy file retrieval | Existing bounded route and byte validation remain; fictional regression tests pass | **PROVIDER UNVERIFIED** for any real tenant. |
| Modern file retrieval | Public route is documented, but this CLI deliberately does not select it | **BLOCKED** pending separate review and owner-approved evidence. |

Developers can run fictional tests with `uv run pytest tests/test_content_tree.py
tests/test_syllabus_discovery.py -q`, then `uv run pytest -q`. These tests use no institution
account. Future live checks require the separate authorization and privacy rules in the
[validation plan](M1_PROVIDER_VALIDATION_PLAN.md); this decision record grants none.
