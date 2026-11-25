# M3 provider validation readiness: content and syllabus

**Verdict:** GO for review of this offline readiness package. **NO-GO for live
requests in M3.** Implementation controls and fictional tests support proposing a
small, owner-operated compatibility check later; they do not establish compatibility
with any institution. Every future request requires separate authorization under the
gates below. This document supplements the [M1 provider validation plan](M1_PROVIDER_VALIDATION_PLAN.md),
[M1.2 TOC decision](M1_CONTENT_COMPATIBILITY_DECISION.md), and
[M2 file decision](M2_MODERN_FILE_COMPATIBILITY_DECISION.md).

## Starting state and audit scope

- Public repository: `webswe/d2l-student-cli`. Fetched `origin/main` at
  `c57f3bf82790a4415ac504738fbdde8d03988d01` (the M2 squash commit).
  The isolated `feat/m3-provider-validation-readiness` worktree started clean.
- Read the content provider, content and syllabus services, HTTP client, session
  loading and expiry paths, redaction, CLI commands, related fictional tests, and
  the existing compatibility, discovery, and threat-model documents.
- No tenant, account, stored session, credential, academic record, or live
  Brightspace route was accessed. No authentication or production code changed.

Evidence labels here have narrow meanings: **DOCUMENTED** means an explicit
public Brightspace contract; **OFFLINE VERIFIED** means a fictional fixture or
mocked transport exercised local code; **PROVIDER UNVERIFIED** requires an
approved tenant; **BLOCKED** means this phase forbids the next check;
**CONTRADICTED** means evidence rules out a proposed inference. Several labels
can apply to one capability. Passing tests never supplies live evidence.

## Capability and evidence matrix

| Capability | Contract evidence | Offline tests / implementation | Live evidence | Remaining gate |
|---|---|---|---|---|
| Version discovery | **DOCUMENTED:** anonymous `GET /d2l/api/versions/` advertises product versions. | **OFFLINE VERIFIED:** malformed, duplicate, unsupported, modern and legacy advertisements; bounded selection. | **PROVIDER UNVERIFIED** | B: confirm one tenant's advertised LE versions. Session acceptance from this response is **CONTRADICTED** as an inference. |
| Authenticated TOC GET | **DOCUMENTED:** `GET /d2l/api/le/{version}/{orgUnitId}/content/toc`, `content:toc:read`. | **OFFLINE VERIFIED:** course ID, request path, version choice, safe errors. | **PROVIDER UNVERIFIED** | C: independently prove approved session and student permission at this route. |
| TOC parser | **DOCUMENTED:** `ToC.TableOfContents` with nested `Modules` and `Topics`. | **OFFLINE VERIFIED:** shape, identifiers, course binding, duplicates, cycles, 10-level and 10,000-node bounds. Completeness reports `unknown`. | **PROVIDER UNVERIFIED** | C: compare field names/types and owner-known synthetic hierarchy; do not infer exhaustive content. |
| Topic visibility | **DOCUMENTED:** hidden, broken, locked and date fields exist; TOC options can alter visibility. | **OFFLINE VERIFIED:** hidden modules/topics and broken topics excluded; locked topics retained; no visibility override parameters. | **PROVIDER UNVERIFIED** | C: compare synthetic visible/hidden/date-restricted cases and permissions. |
| Modern file GET | **DOCUMENTED:** file topic route at current LE 1.82+; scope `content:file:read` at LE 1.35+. | **OFFLINE VERIFIED:** bounded advertised 1.82–1.120 selector, explicit fetch, course/topic binding, 8 MiB stream bound. | **PROVIDER UNVERIFIED** | D: separate approval and one harmless synthetic file. |
| Legacy file fallback | **DOCUMENTED:** old route range and deprecation status; no tenant guarantee. | **OFFLINE VERIFIED:** independent highest advertised 1.30–1.75 fallback, no failure-triggered retry or 1.76–1.81 selection. | **PROVIDER UNVERIFIED** | D only if the approved tenant advertises a legacy-only supported version. |
| PDF validation | No public guarantee that returned bytes are a well-formed PDF. | **OFFLINE VERIFIED:** allowed media type, `%PDF-` prefix and size checks; **not** full PDF structural validation. | **PROVIDER UNVERIFIED** | D: synthetic PDF only; record type/validation outcome, never bytes. Treat structural integrity beyond the signature as unknown. |
| DOCX validation | No public guarantee of safe document structure. | **OFFLINE VERIFIED:** ZIP/XML, part-count/size/ratio, embedded object and macro protections. | **PROVIDER UNVERIFIED** | D: synthetic DOCX only if separately approved; retain no bytes. |
| UTF-8 text validation | No public guarantee of media type or encoding. | **OFFLINE VERIFIED:** allowed media type, UTF-8 decode, NUL rejection and size bound. | **PROVIDER UNVERIFIED** | D: synthetic text only if separately approved. |
| Error handling | **DOCUMENTED:** TOC and file routes list 400/403/404/429 outcomes. | **OFFLINE VERIFIED:** 401/403/404/429, redirects, malformed media/length/body and sanitized diagnostics; no automatic retry. | **PROVIDER UNVERIFIED** | C/D: record naturally observed numeric status and CLI code; stop on unexpected results. Do not manufacture permission failures. |
| Session-expiry handling | Browser-cookie acceptance at these routes is not established by the public contract. | **OFFLINE VERIFIED:** 401 and recognized login-interstitial/redirect cases fail as authentication errors; other redirects fail closed. | **PROVIDER UNVERIFIED** | C and separately D: verify acceptance at each approved authenticated route. |
| GET side effects | Public route descriptions do not guarantee absence of server-side changes. | **OFFLINE VERIFIED:** client issues GET only; this says nothing about provider state. | **PROVIDER UNVERIFIED** / live experiment **BLOCKED** in M3 | E: authoritative statement or safe, non-invasive observation; otherwise `UNKNOWN` and no state-preserving claim. |

## Official contract findings and limits

The [API Properties reference](https://docs.valence.desire2learn.com/res/apiprop.html)
documents anonymous version discovery and a `ProductVersions` response containing
`ProductCode`, `LatestVersion`, and `SupportedVersions`; it lists 200, 403, and
429. Thus a successful versions request establishes advertised availability
only. It cannot prove that a browser cookie is accepted at a content route.

The [Content API reference](https://docs.valence.desire2learn.com/res/content.html)
documents TOC and file GET routes at current LE 1.82+, deprecated 1.75–1.81,
and obsolete 1.74 or earlier. The CLI deliberately selects the highest
advertised LE 1.82–1.120 for each route, or the highest advertised 1.30–1.75
when no bounded modern version is available. It never selects 1.76–1.81 or a
future version above 1.120, and never guesses a version after route failure.
The selected versions are local policy, not a promise that a tenant accepts
them. Older versions remain a compatibility fallback even though the public
reference calls some of them obsolete.

The TOC route documents `content:toc:read`, a `ToC.TableOfContents` with
`Modules` and `Topics`, and 200/400/403/404/429 responses. Topic fields
include `TopicId`, `Identifier`, `Title`, `IsHidden`, `IsBroken`, `IsLocked`,
availability dates, and `Url`. The optional `ignoreDateRestrictions` and
another-user `userId` controls are not used by the CLI. The parser validates
identities and structure, drops hidden and broken entries, leaves locked
entries for the provider's own access rules, and reports completeness as
`unknown`. An empty TOC is not evidence that no content exists.

The file route documents `content:file:read` for LE 1.35+ and
`content:topics:read` for LE 1.34 and earlier. It describes a file-topic
response and an optional `stream` parameter that changes content disposition;
the CLI does not set `stream` or trust the response filename. The route lists
200, 400 (not a file), 403, 404, and 429. No public description establishes
browser-cookie acceptance, the student's permission on a particular course,
response MIME type, tenant file delivery behavior, or absence of server-side
effects. The referenced scopes describe the published API; this phase does
not request scopes or change authentication.

## Implementation and adversarial audit

`d2l content toc --course <id> [--json]` and `d2l content tree` require a
stored session and use version discovery followed by one TOC GET. `d2l
syllabus find --course <id> [--json]` also uses the TOC and does not download.
`d2l syllabus fetch --course <id> --topic <id> --json` requires a listed,
visible file topic, then fetches one file: version discovery, TOC, file GET
are the expected three requests in one invocation. A provider instance caches
version discovery within that invocation. Authentication is loaded before the
first request, including version discovery; the CLI does not expose a separate
anonymous versions-only command. The future Gate B check therefore needs its
own separately approved, tightly scoped method; do not run a content command
and call its versions response an isolated session check.

The content path uses the GET-only HTTPS client with TLS verification,
relative same-origin targets, identity encoding, bounded streaming, and
redirect following disabled. The file GET is allowed only after TOC-based
course/topic verification. Neither a TOC `Url` nor `Content-Disposition`
becomes a request target; file types are PDF, DOCX, and UTF-8 text only. The
service retains bytes in memory and never writes a document to disk. Errors
use fixed, sanitized messages rather than raw URLs, response bodies, cookies,
or filenames. Tests cover malformed discovery, unsupported versions, bad TOC
identities/structure, hidden and broken items, redirects, auth errors, 429,
malformed/truncated/oversized file responses, invalid media and documents,
hostile filenames, and request counts. No confirmed offline security defect
required a source or test change in M3.

**Residual limits:** The PDF check establishes a `%PDF-` prefix and allowed
media type, not full document integrity. Missing `Content-Length` is allowed
under the 8 MiB streaming bound; a present value must be decimal and match
received bytes. TOC omission, date-release behavior, browser-cookie support,
and GET side effects remain provider unverified. `syllabus fetch --json`
returns base64 document bytes, and content JSON includes titles and IDs:
future operators must not capture or publish stdout as evidence.

## Proposed owner-operated sequence — no execution in M3

These are separate approval gates, not a standing authorization. Prefer an
institution-provided test tenant, synthetic student, test course, and small
harmless synthetic syllabus. Use no real student's unread content or document.
Set a strict aggregate ceiling **before** work. A proposed maximum for the
three separate checks below is six GETs (one for B, two for C, three for D),
with no retries or extra probes. The owner must approve that ceiling and the
actual route versions; stop rather than improvise if the sequence differs.

### Gate A — authorization and environment

- Owner identifies the tenant privately and confirms institutional permission,
  test or production classification, synthetic test account and student role,
  authorized test course, synthetic file/topic, approved time window, route
  templates, allowed LE versions, maximum request count, private evidence
  retention/deletion policy, and a named decision maker.
- Owner confirms the authentication path is permitted for that test account.
  No new auth method, permissions, visibility overrides, or credential export.
- Define stop and containment steps in advance. If no suitable synthetic
  environment or permission exists, **NO-GO**. No approval means no request.

### Gate B — version discovery, separately authorized

Propose one `GET /d2l/api/versions/` without a stored session or credentials,
using only an owner-approved tool and tenant. Record product/version metadata
and exact numeric status. Check whether the advertised LE versions intersect
the CLI's bounded ranges. A successful response does **not** pass the session
gate; 403, 429, malformed data, or unsupported ranges stop this attempt.
Do not create a general live probing utility for this check.

### Gate C — authenticated TOC, separately authorized

After B and explicit approval, propose one `d2l content toc --course <synthetic-id>
--json` invocation in the synthetic course; its expected network scope is
versions plus one TOC GET. Verify session acceptance **at the TOC route**,
student permission, `ToC.TableOfContents` structure, course association,
module/topic identity and safe filtering using only owner-known synthetic
records. Record field names/types and counts or completeness limits, never
titles, identifiers, URLs, or response bodies. Do not save terminal output.
An unexpected extra request, redirect, shape, exposure, or status is NO-GO.

### Gate D — file route, separate authorization decision

Only after a satisfactory C result, propose one explicit `d2l syllabus fetch
--course <synthetic-id> --topic <synthetic-id> --json` for an approved file
topic. Expected scope is versions, TOC, then one file GET; the file route must
independently accept the session and student permission. Verify course/topic
binding, status, media type, content-length behavior, no redirect, 8 MiB bound,
and local validation outcome. **Do not retain or publish CLI stdout:** JSON
contains base64 document bytes. Do not store the bytes or screenshots. A
legacy-only check is relevant only if the tenant actually advertises a
supported legacy version. Unexpected content, permission denial, 429, an
unexpected extra request, or uncertain binding is NO-GO.

### Gate E — side-effect assessment

Before claiming any GET is state-preserving, seek authoritative provider
documentation or a trustworthy, non-invasive observer whose own read behavior
has been established. Compare only synthetic account/course state if a future
experiment is separately approved. This phase performs no such experiment.
If evidence is unavailable or the observer could alter state, record
`UNKNOWN`; do not infer safety from GET or a 200. An observed state change is
NO-GO and stops further checks.

## Private evidence template and redaction

Copy this **blank** template to a private, access-limited location only after
authorization. Completed evidence must never be committed to this repository.

```text
Test date (YYYY-MM-DD):
Tenant type (test / production; no hostname):
Account role (no account name or ID):
API product and version:
Route template (placeholders only):
Exact numeric HTTP status code:
Session accepted (yes / no / unknown):
Structural field names and types (no values):
CLI outcome code (no raw output):
Side-effect assessment (unchanged / changed / unknown; basis only):
Completeness limitations (structure and counts only):
Decision (GO / LIMITED GO / NO-GO) and approver:
```

Never retain tenant hostnames, course/topic/student identifiers, names,
credentials, cookies, tokens, headers, raw request URLs, response bodies,
academic content, document bytes, or screenshots of private content. Do not
paste actual command output or full error traces. Record the exact numeric
HTTP status, not merely a status class. If a safe observation cannot be made
without recording prohibited data, mark it `unknown`.

## Stop conditions, remaining unknowns, and next decision

Stop at missing permission or synthetic prerequisites; unexpected request
count, route or version; malformed discovery; authentication challenge;
403/404/429; redirect; changed or unrecognized response structure; wrong
course/topic binding; unexpected restricted content; excess bytes; unsafe
document type; suspected server-side state change; or a need to inspect
credentials or broaden scope. Preserve no raw response as a workaround.

Implementation readiness is supported by the offline suite; provider
compatibility is **PROVIDER UNVERIFIED** for session acceptance, permissions,
actual response structure, visibility, file delivery, rate limits, and GET
side effects. The owner should first decide whether an institution-approved
synthetic test environment and an authorized one-request Gate B are available.
Only then should a separate Gate C decision be considered. Gate D requires its
own later approval. No live compatibility decision or production use is
endorsed by this document.

## Offline verification record

This document was prepared from source, public contracts and fictional tests.
The focused content, syllabus, HTTP, session, and redaction run passed **364**
tests. The full local run passed **1,580** and failed **5** existing
terminal-color assertions in `tests/test_cli_output_environment.py` under
this managed Windows console. A separate run excluding that entire module
passed **1,571**; that exclusion is not a full-suite pass. No tests were
skipped. These outcomes do not indicate a content or syllabus failure.

`ruff check .` passed and strict mypy found no issues in 50 source files.
`ruff format --check .` reported 25 previously existing Python files that it
would reformat; M3 changes no Python file. The document's three relative links
resolved. No new regression was needed because the audited adversarial cases
already had focused coverage. No live provider test was run. Git diff and CI
results, including any later failures, belong in the pull request.
