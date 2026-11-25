# Milestone 1 offline provider engineering review

This review uses public Brightspace documentation, source inspection and fictional offline tests
only. **No tenant, browser session, credential store or real academic record was accessed.**
"Documented" below means a published route contract, not evidence that a particular institution
accepts this CLI's browser cookie or returns the expected shape. All live compatibility decisions
remain **UNVERIFIED**. The approval boundary and integration go/no-go gates remain in the
[provider validation plan](M1_PROVIDER_VALIDATION_PLAN.md).

## Audit findings and disposition

| Component | Claimed behavior and existing coverage | Risk / severity | Disposition | Provider evidence? |
|---|---|---|---|---|
| Shared LE pages | Fictional tests cover arrays, object pages, malformed shapes and `Next` cursors. | A missing or empty-string `Next` was interpreted as no next page, potentially claiming a partial calendar, quiz or forum listing complete. **High.** | Reject missing or empty `Next` on object pages; new cross-provider regression tests. | No for the fix; yes for actual paging semantics. |
| Calendar aggregation | Existing tests cover UTC windows, DST, all-day spans, occurrences, fallback and errors. | Equal event and occurrence IDs from different courses were collapsed without a course key, losing an association while reporting completion. **High.** | Include course ID in deduplication; update tests and output docs. | No for the fix; yes for provider ID scope. |
| Calendar routes | Fictional tests cover LE 1.94 occurrence routing, 403/404 fallback and incomplete recurrence definitions. | Whether cookies, shapes, date boundaries and occurrence pages match a tenant is unknown. **High.** | Keep bounded fallback and incomplete markers; validate later under approval. | Yes. |
| Quiz listing | Fictional tests cover nullable fields, hidden descriptions, empty lists, paging and errors. | Student-role visibility and release meaning vary by tenant. **Medium.** | Preserve conservative output and separate listing gate. | Yes. |
| Quiz identity and attempts | Tests require same-session `whoami`, filtered `userId`, and matching `UserId`/`QuizId` on every attempt; scores and feedback stay hidden. | Browser-cookie acceptance, LP/LE ID equivalence and student permission remain unproven. **Critical if guessed.** | Preserve fail-closed behavior; no unfiltered fallback. | Yes. |
| Discussion posts | Tests cover default refusal before config/auth, one-invocation flag, bounded pages, identity associations, sanitized output and stderr warning. | The posts GET may change server-side read state. **Critical.** | Keep posts no-go pending independent read-state evidence and separate approval. | Yes. |
| Content hierarchy | Fictional tests cover nesting, cycles, duplicates, hidden nodes, bounds and unknown completeness. | The selector accepts LE 1.30–1.75, while the current public TOC reference marks 1.82+ current and older versions deprecated/obsolete. A 1.82-only tenant fails closed. **High compatibility risk.** | Document the version/shape gap; do not widen versions based on documentation alone. | Yes for tenant versions and shape. |
| Course resolution | Tests cover exact IDs, ambiguity, hidden courses, Unicode and deterministic stage order. | Matching still depends on the quality and completeness of the course list. **Medium.** | Keep ambiguous matches rejected; validate course-list completeness separately. | Yes. |
| HTTP and auth boundary | Existing tests cover GET-only methods, HTTPS/TLS, same origin, redirect refusal, response bounds, fixed errors and redaction. | A GET method does not establish freedom from server-side state changes. **High for posts.** | Preserve boundary; do not infer read-state safety. | Yes for server effects. |

The two corrected defects were reproduced with fictional failing tests before changing source.
No new dependencies, authentication paths, write APIs, downloads or live-probing commands were
added. The existing suite remains the reusable offline transport/fixture infrastructure; the new
edge-case module adds only deterministic, fictional data.
The [published object-page convention](https://docs.valence.desire2learn.com/basic/apicall.html)
defines `Next` as an API URL or `null`; missing and empty-string values are not supported end
markers on an object page.

## Provider compatibility matrix

Version ranges below describe **current implementation selection**, not guaranteed tenant
support. The public contracts linked in the last column support route and schema research only.
Every row's live status is **UNVERIFIED**; a synthetic test pass is never a provider pass.

| Capability | Product, route template | Auth context; expected structure and pagination | Offline status / limitation | Live gate and evidence | Public contract |
|---|---|---|---|---|---|
| Calendar base | LE 1.82–1.120; `/d2l/api/le/{v}/{org}/calendar/events/myEvents/` | Approved student session; `EventDataInfo` array or page; recurring series definitions only. | Fixture-tested; page signal means incomplete. | UNVERIFIED: accepted session, UTC/day semantics, shape, permission and page behavior. | [Calendar](https://docs.valence.desire2learn.com/res/calendar.html) |
| Calendar occurrences | LE 1.94–1.120; `/d2l/api/le/{v}/calendar/events/myEventsWithOccurrences/` | Approved student session; `EventWithOccurrencesInfo` with `Occurrences`; `ObjectListPage`. | Fixture-tested; 403/404 fallback; missing expansion or next page cannot imply a complete schedule. | UNVERIFIED: occurrence identity, recurrence coverage, window and fallback meaning. | [Calendar](https://docs.valence.desire2learn.com/res/calendar.html) |
| Quiz listing | LE 1.82–1.120; `/d2l/api/le/{v}/{org}/quizzes/` | Approved student session; quiz metadata in `ObjectListPage`. | Fixture-tested; `Next` means incomplete; empty is not proof of no quizzes. | UNVERIFIED: student visibility, field shapes and session acceptance. | [Quizzes](https://docs.valence.desire2learn.com/res/quiz.html) |
| Identity and own attempts | LP 1.49–1.120 `users/whoami`; LE 1.82–1.120 `/d2l/api/le/{v}/{org}/quizzes/{quiz}/attempts/?userId=...` | Same approved session; one `Identifier`, then filtered `QuizAttemptData` page; each record must match user and quiz. | Fixture-tested; no identity or owner proof means no attempt output; score never displayed. | UNVERIFIED: cookie acceptance, ID equivalence, filter enforcement and permission. | [Users](https://docs.valence.desire2learn.com/res/user.html), [Quizzes](https://docs.valence.desire2learn.com/res/quiz.html) |
| Discussion forums/topics | LE 1.82–1.120; `/d2l/api/le/{v}/{org}/discussions/forums/`, `.../forums/{forum}/topics/` | Approved student session; forum/topic lists with bounded items. | Fixture-tested; page markers do not imply completeness. | UNVERIFIED: session, student permission, associations and list shapes. | [Discussions](https://docs.valence.desire2learn.com/res/discuss.html) |
| Discussion posts | LE 1.82–1.120; `.../topics/{topic}/posts/` | Separate explicit approval; one-invocation flag; bounded pages of 100 until empty page or cap. | Fixture-tested guard, parser and pagination; **read-state effect unknown**. | **NO-GO for recommendation** until independent, trustworthy read-state evidence and owner approval. | [Discussions](https://docs.valence.desire2learn.com/res/discuss.html) |
| Content hierarchy | LE 1.30–1.75 selected; `/d2l/api/le/{v}/{org}/content/toc` | Approved student session; `TableOfContents` with nested modules/topics; no explicit completeness proof. | Fixture-tested parser; current version ceiling may reject modern-only tenants. | UNVERIFIED: version, cookie, current schema, visibility and TOC completeness. | [Content](https://docs.valence.desire2learn.com/res/content.html) |
| Course resolution | Existing course-list route, then local resolver | Complete accessible-course list; exact ID/code/name precedes prefix/substring; ties rejected. | Fictional resolver tests pass; no extra provider request. | UNVERIFIED: course-list completeness and tenant-specific naming. | [Provider notes](PROVIDER_NOTES.md) |

The published docs specify `userId` as an optional quiz-attempt filter and `whoami` as a
current-user route; they do **not** establish browser-cookie acceptance or the equivalence of
`whoami.Identifier` and attempt `UserId` for this client. The published discussion-post GET
contract does **not** establish that retrieval leaves read state unchanged. The published content
TOC contract lists LE 1.82+ as current and a `TableOfContents` response; the implemented older
version ceiling is a deliberate validation blocker pending shape evidence.

## Private evidence template (blank only in this repository)

Copy this template to a **private, access-limited location after separate approval**. Never
commit a filled-in copy to the public repository.

| Safe field | Observation |
|---|---|
| Date (UTC) | |
| Tenant classification (test/production; no hostname) | |
| Test role (no account name or ID) | |
| Approved route template (no real IDs or query values) | |
| API product and version | |
| Exact numeric HTTP status code | |
| Authenticated route accepted the approved session? (yes/no/unknown) | |
| Structural field names and types only | |
| Pagination/completeness observation (including unknown) | |
| Read-state assessment (unchanged/changed/unknown; method validated?) | |
| Decision and unresolved question | |

Do not record credentials, cookies, tokens, headers, raw or full URLs, query values, response
bodies, screenshots, identifiers, names, grades, quiz content, attempts, discussion text or other
academic information. A successful `/d2l/api/versions/` request establishes available versions,
not acceptance of the session at an authenticated route.

## Owner authorization checklist for a first live step

This checklist is **preparatory and grants no authorization**. The owner must fill it privately
before any request, preferably for a test tenant and synthetic student:

- [ ] Identify the specific tenant, account and course privately; confirm institutional permission.
- [ ] Specify the exact route, API version, parameters and bounded time window. Start with version
  discovery only; approve each authenticated route separately. Do not include posts in this step.
- [ ] Identify possible exposed fields and decide how the operator will avoid retaining them.
- [ ] Approve only the safe evidence fields above, a time limit and an operator. No raw capture.
- [ ] Define stop conditions: redirect, auth/permission denial, unexpected shape, unexpected data,
  rate limit, read-state change or uncertain side effect. Stop instead of expanding scope.
- [ ] Define containment if state changes: cease requests, preserve the safe observation only,
  notify the responsible tenant owner through an approved channel, and follow the tenant's own
  procedure. Do not attempt an unapproved "reversion" request.

## Readiness decision

| Capability | Decision now |
|---|---|
| Calendar route behavior | Ready for separately approved, bounded test-account validation; live compatibility UNVERIFIED. |
| Calendar completeness | UNVERIFIED until occurrence, fallback, date and pagination semantics are observed. |
| Quiz listing | Ready for separate test-account validation; student visibility UNVERIFIED. |
| Quiz attempts | NO-GO until same-session identity, ID equivalence, filter and ownership are independently established. |
| Discussion forums/topics | Ready for separate test-account validation; permissions and shapes UNVERIFIED. |
| Discussion posts | NO-GO until an authoritative, non-state-changing observer establishes read-state effect; acknowledgment alone is insufficient. |
| Content hierarchy | Version/shape gate needed before a compatibility claim; LE 1.82-only tenants currently fail closed. |
| Course resolution | Local ambiguity handling is ready; provider course-list completeness remains UNVERIFIED. |

For a future discussion experiment, require a synthetic topic and synthetic student with the
least permissions needed, plus an administrator-approved observer that can inspect read state
without opening the topic. Prove the observer itself leaves state unchanged on a separate control
first; record pre/post observations without content. Any observer uncertainty, changed state,
unexpected request or inability to restore the controlled test setup yields **NO-GO**. Never use
production students' unread posts. See the full [validation plan](M1_PROVIDER_VALIDATION_PLAN.md).

## Offline reproduction and quality gates

In an existing environment with the repository's locked dependencies, run:

```powershell
uv run pytest tests/test_m1_offline_edge_cases.py tests/test_calendar.py tests/test_quizzes.py tests/test_discussions.py tests/test_content_tree.py tests/test_course_resolver.py -q
uv run pytest -q
uv run ruff check .
uv run mypy
git diff --check
```

These commands use fictional fixtures and mocked transport. Do not add a live endpoint, saved
session or browser login to this workflow. If the dependencies are unavailable, report those
checks as unexecuted; do not install them solely to fill a checklist.
