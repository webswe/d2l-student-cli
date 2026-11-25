# Proposed GitHub roadmap (not filed)

## 2026-11-25 offline development status

The bounded academic snapshot is merged through PR #11, and its Markdown report
is merged through PR #10. Portable agent guidance and offline diagnostics
remain in separate review branches described in
[the development session record](DEVELOPMENT_SESSION_2026-11-25.md).
None of these features
establishes Brightspace compatibility or resolves the provider-evidence
dependencies below. No official final-grade calculation, overdue label,
assignment-detail route, feedback route, or automatic file retrieval was
enabled.

These are issue-ready drafts based on `main` after PR #4 merged at
`75b6d79677f8ffcea2f84db9a774218c7b9141d6`. No issue, label or milestone has been created.
Use only the labels listed below. `S`, `M`, `L` estimate relative implementation complexity.
Each issue must keep the GET-only, same-origin, fail-closed client and fictional-data tests.

Academic coverage implementation for the merged feature scope is complete: calendar, quiz and
discussion commands, content hierarchy and course resolution are in `main`. Synthetic tests and
all five post-merge CI checks passed. These results verify the implementation against fictional
fixtures; they do not verify real Brightspace compatibility. Calendar routes, quiz identity and
attempt ownership, and discussion read-state effects still require separate, explicit owner approval
for live provider validation. Use the [Milestone 1 provider validation plan](M1_PROVIDER_VALIDATION_PLAN.md)
to collect limited evidence and record a go/no-go decision. Discussion-post retrieval already
requires `--acknowledge-read-state-risk` on each invocation; whether its GET changes server-side
read state remains unknown.

## Milestone 1 — Core Academic Coverage

### Validate experimental calendar routes against a consenting tenant

**Problem/value:** Calendar commands are mock-tested but route/session behavior is unconfirmed;
validation would make real scheduling dependable. **Scope:** Value-free route and shape checks for
course-scoped events and occurrences, with the current fallback and completeness rules.
**Out:** Real academic records in reports, date inference, or new write calls.
**Accept/test:** Document supported versions, cookie acceptance, recurrence and page behavior;
add fictional fixtures for every observed variation and retain bounded windows.
**Security/provider evidence:** Owner-operated read-only checks only; record status, schema and
read-state effects without storing event content. **Depends:** owner-approved live validation.
**Priority/labels/size:** P0; `type:testing`, `area:academics`, `priority:P0`,
`status:needs-provider-evidence`; M.

### Validate experimental quiz listing and attempt ownership

**Problem/value:** Quiz list and own-attempt commands exist but identity mapping and release
semantics are unconfirmed. **Scope:** Verify student visibility, `whoami`/`UserId` equivalence,
filtered attempt ownership and empty/error distinctions. **Out:** Question, answer or score
disclosure, starting/submitting a quiz. **Accept/test:** Preserve fail-closed identity checking;
document observed nullable fields and add synthetic mismatch and permission fixtures.
**Security/provider evidence:** Owner-operated, value-free, read-only route/schema evidence;
do not record attempts or grades. **Depends:** owner-approved validation.
**Priority/labels/size:** P0; `type:testing`, `area:academics`, `priority:P0`,
`status:needs-provider-evidence`; M.

### Establish whether discussion post GET changes read state

**Problem/value:** The existing posts command is experimental; the documented GET contract does
not settle read-state side effects. **Scope:** Review official contract or controlled owner test,
then retain or gate post browsing based on evidence. **Out:** Mark-read, post, reply or rating
operations. **Accept/test:** Document a defensible decision; if side effects occur, block the
route and test that no posts GET is sent. Validate pagination, topic/forum associations and
safe bodies with fictional fixtures. **Security/provider evidence:** Explicit read-state evidence
is required before calling post browsing safe. **Depends:** owner-approved validation.
**Priority/labels/size:** P0; `type:security`, `area:academics`, `priority:P0`,
`status:needs-provider-evidence`; M.

### Validate content hierarchy and course resolver in a complete environment

**Problem/value:** Both are merged and synthetic tests pass, but TOC browser-session compatibility
is unverified. **Scope:** Check module identity and TOC shape in an owner-approved environment.
**Out:** File downloads or automatic fuzzy selection. **Accept/test:** Preserve passing tests for
nested, duplicate, cyclic, hidden and ambiguous cases; confirm one versions plus one TOC GET;
old numeric commands keep their schemas.
**Security/provider evidence:** Value-free TOC shape and session support only; no stored content.
**Depends:** separately approved provider validation.
**Priority/labels/size:** P0; `type:testing`, `area:content`, `priority:P0`,
`status:needs-provider-evidence`; M.

## Milestone 2 — Academic Intelligence

### Add an all-course grades command and official final-grade representation

**Problem/value:** `overview` already retrieves grade items across courses, but users lack a
dedicated grades view and cannot distinguish an official final from an estimate.
**Scope:** Reuse overview grade service; show provider-displayed final grades only after a verified
field contract. **Out:** Calculating weighted grades from display strings.
**Accept/test:** Stable course association, explicit `unknown` completeness, no duplicate backend;
fictional mixed-course, absent-final and error tests. **Security/provider evidence:** Confirm the
official final-grade source and release visibility; protect private grade data.
**Depends:** verified final-grade surface for that subfeature.
**Priority/labels/size:** P1; `type:feature`, `area:academics`, `priority:P1`,
`status:needs-provider-evidence`; M.

### Read assignment details and availability dates

**Problem/value:** List rows omit full instructions and start/end availability.
**Scope:** Explicit detail command and distinct start, end and due fields.
**Out:** Submission or interpreting availability as deadline. **Accept/test:** Preserve the list
schema; sanitize instructions, validate course/assignment identity and nullable dates; synthetic
malformed HTML, missing dates and failure tests. **Security/provider evidence:** Verify a read-only
detail route, date semantics and body bounds. **Depends:** provider evidence.
**Priority/labels/size:** P1; `type:feature`, `area:academics`, `priority:P1`,
`status:needs-provider-evidence`; L.

### Read grade feedback content safely

**Problem/value:** Only feedback availability is shown. **Scope:** User-requested detail for
released feedback. **Out:** Unreleased/private feedback or raw HTML rendering.
**Accept/test:** Course/item binding, sanitized text, nullability and error separation; fictional
release, malformed, oversized and injection tests. **Security/provider evidence:** Verified student
visibility and read-only route; strict private-data handling. **Depends:** provider evidence.
**Priority/labels/size:** P1; `type:feature`, `area:academics`, `priority:P1`,
`status:needs-provider-evidence`; M.

### Add announcement metadata and cross-course news

**Problem/value:** Current News listing has title/body but little metadata and no dedicated
cross-course news command. **Scope:** Verified dates, author and pinned/urgent flags; aggregate
existing per-course service with source ordering and explicit completeness.
**Out:** Mark-read calls or invented timestamps. **Accept/test:** Backward-compatible list JSON;
fictional ordering, source association, nulls and partial failure tests.
**Security/provider evidence:** Confirm fields and side-effect-free read status before exposing it.
**Depends:** metadata evidence; existing course/announcement services.
**Priority/labels/size:** P1; `type:feature`, `area:academics`, `priority:P1`,
`status:needs-provider-evidence`; M.

### Add upcoming/overdue filtering and chronological assignment summaries

**Problem/value:** Chronological overview exists, but ambiguous dates cannot safely support due
labels. **Scope:** Verified timezone/status semantics, explicit calculated labels, optional filters.
**Out:** Treating timezone-naive display values as local instants.
**Accept/test:** Preserve current overview schema by default; test offsets, DST, null and
ambiguous dates, completed work and boundary instants. **Security/provider evidence:** Confirm
source date and completion semantics before overdue classification. **Depends:** provider evidence.
**Priority/labels/size:** P1; `type:feature`, `area:academics`, `priority:P1`,
`status:needs-provider-evidence`; L.

### Add honest incremental updates

**Problem/value:** Repeated full reads are costly; local snapshot differences do not prove all
remote changes. **Scope:** `--since` only for fields/routes with verified timestamps or tokens.
**Out:** Claiming complete change history from two local snapshots.
**Accept/test:** Per-source completeness, stable cursors, bounds, clock and deletion cases;
fictional missed-page and malformed-token tests. **Security/provider evidence:** Verify server
change semantics and keep cached records opt-in. **Depends:** timestamp/token evidence.
**Priority/labels/size:** P2; `type:feature`, `area:academics`, `priority:P2`,
`status:needs-provider-evidence`; L.

### Add privacy-conscious user profile and unread update counts

**Problem/value:** Users cannot inspect safe account context or unread counts.
**Scope:** Minimal own-profile metadata and counts only where a genuinely read-only route exists.
**Out:** Printing identifiers/contact details by default or triggering mark-read.
**Accept/test:** Default redaction, nullable fields, no count fabrication, fictional expiry and
permission tests. **Security/provider evidence:** Verify own-user binding and no read-state side
effect. **Depends:** identity provider and route evidence.
**Priority/labels/size:** P2; `type:feature`, `area:academics`, `priority:P2`,
`status:needs-provider-evidence`; M.

## Milestone 3 — Agent Integration

### Compose an academic snapshot

**Problem/value:** Agents need one bounded academic JSON document.
**Scope:** Compose verified services and expose per-source completeness/request accounting.
**Out:** Calling it a complete academic state or persisting it implicitly.
**Accept/test:** Stable envelope, deterministic order, stream separation, bounded course count,
fictional partial/error tests. **Security/provider evidence:** No new endpoint; treat all returned
text as untrusted data. **Depends:** core service validation.
**Priority/labels/size:** P1; `type:feature`, `area:agents`, `priority:P1`; M.

### Add optional Markdown output and standing agent documentation

**Problem/value:** JSON is stable but humans and agents also need readable summaries and safe
consumption guidance. **Scope:** Optional Markdown formatter and documented subprocess/privacy
rules. **Out:** HTML execution, prompt-instruction authority or changed JSON.
**Accept/test:** Escape tables/links/control text; test hostile strings, nulls and output streams;
document exit checking and content-as-data. **Security/provider evidence:** No new endpoints.
**Depends:** machine-interface contract. **Priority/labels/size:** P1; `type:documentation`,
`area:agents`, `priority:P1`; M.

### Generate a portable agent skill and optional course onboarding files

**Problem/value:** Users repeat command and privacy guidance in agent setups.
**Scope:** Explicit user-initiated generation of a supported-command skill and optional local
course preferences/SOP. **Out:** Hidden writes, saved academic records or commands outside the
read-only contract. **Accept/test:** Previewable output, chosen destination, no overwrite by
default, fictional path traversal and prompt-injection tests.
**Security/provider evidence:** No new endpoint; untrusted course text never becomes instruction.
**Depends:** standing agent documentation. **Priority/labels/size:** P2; `type:feature`,
`area:agents`, `priority:P2`; M.

## Milestone 4 — Content Access

### Design and implement explicit attachment and module-file downloads

**Problem/value:** Students need selected assignment attachments and lecture files.
**Scope:** Separate user-requested commands using verified file routes and the existing bounded
client. **Out:** Arbitrary URL download, recursive fetching or automatic downloads from `tree`.
**Accept/test:** Exact course/topic binding, safe media types, size limits, destination control,
collision handling, no partial files; fictional redirects, MIME mismatch and oversized tests.
**Security/provider evidence:** Dedicated file threat review and endpoint/origin validation.
**Depends:** content hierarchy and detail-route evidence. **Priority/labels/size:** P1;
`type:security`, `area:content`, `priority:P1`, `status:needs-provider-evidence`; L.

### Validate local file handling and SimpleSyllabus integration

**Problem/value:** External syllabus and downloaded files require distinct trust decisions.
**Scope:** Filename/path hardening and a separate SimpleSyllabus route/origin investigation.
**Out:** Cross-origin credential forwarding, active document execution or silent external calls.
**Accept/test:** Synthetic traversal, symlink, collision, MIME and size tests; explicit consent
for any cross-origin access. **Security/provider evidence:** Verify origin, authentication and
response contract before integration. **Depends:** download design.
**Priority/labels/size:** P2; `type:security`, `area:content`, `priority:P2`,
`status:needs-provider-evidence`; L.

## Milestone 5 — Setup and Distribution

### Add setup and doctor commands

**Problem/value:** First-run configuration and diagnostics are manual.
**Scope:** Interactive validated HTTPS origin setup and read-only checks with actionable errors.
**Out:** Capturing passwords or printing session material. **Accept/test:** Invalid origins,
expired session, missing keyring and offline cases; no secrets in stdout/stderr.
**Security/provider evidence:** Reuse existing origin/session boundary.
**Depends:** configuration review. **Priority/labels/size:** P1; `type:feature`, `area:cli`,
`priority:P1`; M.

### Investigate browser channel and cross-platform credential support

**Problem/value:** Current browser and keyring behavior limits portability.
**Scope:** Opt-in installed-browser channel and macOS/Linux credential-backend evaluation.
**Out:** Plaintext fallback, silently shared browser profiles or MFA automation.
**Accept/test:** Session isolation, storage failure and platform matrix with fictional tests.
**Security/provider evidence:** Threat review of cookies, profile lifetime and platform stores.
**Depends:** auth architecture. **Priority/labels/size:** P2; `type:security`, `area:auth`,
`priority:P2`; L.

### Choose a unique distribution name and design release automation

**Problem/value:** `d2l-cli` conflicts with an existing published distribution.
**Scope:** Select a distinct name, plan metadata migration and guarded release checks.
**Out:** Renaming or publishing in this milestone; self-update execution.
**Accept/test:** Name availability verified at release time, packaging metadata tests,
reproducible artifacts and human approval gates. **Security/provider evidence:** No provider
route; review supply-chain and publication permissions. **Depends:** owner choice.
**Priority/labels/size:** P1; `type:feature`, `area:distribution`, `priority:P1`; M.

### Design self-update separately

**Problem/value:** Remote code acquisition would add a new trust boundary.
**Scope:** Threat model, signatures/provenance and explicit user approval before any code.
**Out:** Automatic remote execution in the current client. **Accept/test:** Reviewable design and
negative tests before implementation. **Security/provider evidence:** Verified distribution
channel. **Depends:** unique package and release design. **Priority/labels/size:** P2;
`type:security`, `area:distribution`, `priority:P2`; L.

## Milestone 6 — Advanced Authentication and Reliability

### Add offline session-status metadata

**Problem/value:** `auth status` currently makes a live verification request.
**Scope:** Separate offline metadata command reporting only safe origin/expiry presence.
**Out:** Printing cookie/token values or claiming validity without a request.
**Accept/test:** Distinguish present, absent and unverified; fictional redaction tests.
**Security/provider evidence:** No new route. **Depends:** session-store contract.
**Priority/labels/size:** P2; `type:feature`, `area:auth`, `priority:P2`; S.

### Review optional headless login and automatic session refresh

**Problem/value:** Manual SSO can be inconvenient, but institutions differ in MFA and refresh
rules. **Scope:** Two separate threat-reviewed designs with opt-in behavior only.
**Out:** Default headless authentication, credential collection or universal-refresh claims.
**Accept/test:** No weakening of manual-login path, explicit failure/consent handling and
synthetic expiry/refresh tests before code. **Security/provider evidence:** Institution-specific
support and SSO policy review. **Depends:** browser-channel investigation.
**Priority/labels/size:** P2; `type:security`, `area:auth`, `priority:P2`,
`status:needs-provider-evidence`; L.

### Track provider compatibility and completeness limits

**Problem/value:** Different tenants may return alternate versions, shapes and page behavior.
**Scope:** Evidence matrix and bounded fixture suite for supported variants.
**Out:** Silent broad crawling or claiming complete lists from small result counts.
**Accept/test:** Explicit route/version compatibility and per-command completeness; synthetic
pagination, oversized, wrong-course, expired-session and redirect tests.
**Security/provider evidence:** Value-free owner-provided observations, no real academic fixtures.
**Depends:** core provider validation. **Priority/labels/size:** P1; `type:testing`,
`area:academics`, `priority:P1`, `status:needs-provider-evidence`; M.
