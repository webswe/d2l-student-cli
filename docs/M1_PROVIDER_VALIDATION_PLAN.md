# Milestone 1 provider validation plan

Status: **planning only**. The calendar, quiz and discussion commands are merged, covered by
fictional-data tests, and have passed post-merge CI. No live compatibility claim follows from that.
See [Activities](ACTIVITIES.md) for the user-facing command contract and
[Provider notes](PROVIDER_NOTES.md) for the fail-closed client boundary. The issue priorities remain
in the [roadmap](ROADMAP.md).

## Authorization and evidence rules

- This plan authorizes **no live Brightspace request**. Before any future live step, the owner must
  separately approve the tenant, account, exact route or command, time window, and evidence to
  retain. Confirm institutional permission. Stop at any unexpected response, redirect, permission
  denial or read-state change; do not broaden the probe on the fly.
- Prefer an institution-provided test tenant, course and synthetic student account. Keep each
  check small and owner-operated. Do not run discussion-post retrieval merely because its flag
  exists. A GET may have server-side effects.
- Record only: test date, tenant type (test/production, without hostname), role, API product and
  minor version, route *template* (no numeric ids or query values), exact numeric HTTP status
  code, accepted session yes/no, structural field names and types, page/completeness behavior,
  read-state result (unchanged/changed/unknown), CLI outcome code, and decision. Use a private,
  access-limited evidence note. Do not retain raw requests, URLs, headers, cookies, tokens,
  session files, screenshots, response bodies, student or course identifiers, names, event text,
  grades, quiz contents, attempts, discussion text, or other sensitive academic data. Redact before
  making fictional fixtures; inspect fixtures for accidental real values.
- Record **unknown** when a check cannot be made without exposing data or changing state. A 200,
  an empty list, or a GET method alone cannot establish completeness, visibility or lack of side
  effects. File any compatibility change with fictional fixtures and repeat offline tests before
  changing the go/no-go decision.
- A successful `/d2l/api/versions/` request establishes available API versions only; it does not
  prove that the browser session is accepted by an authenticated route. Verify session acceptance
  independently at each relevant, separately approved authenticated route before passing that gate.

## Offline preparation (safe for this milestone)

1. Review the exact CLI contract in [Activities](ACTIVITIES.md), the route assumptions in
   [Provider notes](PROVIDER_NOTES.md), and the provider code under `src/d2l_cli/providers/`.
   Keep the expected shapes and failure cases as a checklist; do not use credentials or sessions.
2. Confirm the local commands and flags against `src/d2l_cli/cli.py`: `d2l calendar list
   --course <id> [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--json]`, `d2l calendar upcoming
   [--days N] [--all-courses] [--json]`, `d2l quizzes list --course <id> [--json]`,
   `d2l quizzes attempts --course <id> --quiz <id> [--json]`, `d2l discussions forums
   --course <id> [--json]`, `d2l discussions topics --course <id> --forum <id> [--json]`,
   and `d2l discussions posts --course <id> --forum <id> --topic <id>
   --acknowledge-read-state-risk [--thread <id>] [--json]`. These are references for a later
   approved run, not instructions to run them now.
3. For each gate below, prepare a one-page observation sheet using only the safe fields above.
   Identify a stop condition and who will make the final go/no-go decision before any live work.

## A. Calendar validation

**Proposed owner sequence after separate approval:** Use a test course with owner-known, synthetic
timed, all-day and recurring events. First check `/d2l/api/versions/` for a supported LE version
(1.82–1.120). At LE 1.94 or later, check the occurrence route for one course and a bounded
window; otherwise check the course-scoped `myEvents/` route. Record acceptance separately for
each route. The client falls back from occurrences to the base route only on 403 or 404; a
fallback is not proof of equivalent recurrence coverage.

Check field names and types against `EventDataInfo` and `EventWithOccurrencesInfo`, including
`Occurrences`, `CalendarEventId`, course association, `StartDateTime`/`EndDateTime`,
`StartDay`/`EndDay`, `IsAllDayEvent`, `IsRecurring`, `RecurrenceId` and optional `EventType`.
Compare owner-known boundary events with the CLI's half-open UTC window `[start, end)`: `--to`
is exclusive, `--days` counts elapsed 24-hour periods, timed events use UTC instants, and all-day
events use local calendar days. Include a daylight-saving boundary and a missing end date. Check
that distinct occurrences are expanded exactly once; a series definition must remain visibly
unexpanded. Inspect whether `Next` or any other page signal appears and whether the result is
marked incomplete; the current client does not follow calendar pages. Check 401/403/404 and
malformed response handling only where naturally observed or reproduced offline with fictional
fixtures; do not force failures by changing access controls on a live course.

**Calendar go/no-go**

- [ ] GO: Supported LE version and approved browser-session acceptance are observed for each
  route used; exact event shapes, course binding, window boundaries, timezone and all-day meaning
  are confirmed with synthetic events.
- [ ] GO: Recurrence coverage is accurate for the selected route, and any fallback, unexpanded
  series or page signal is reported as incomplete. Permission and error outcomes fail closed.
- [ ] NO-GO or limited scope: Session rejection, unknown date semantics, unrecognized shape,
  missing occurrences, ambiguous paging or a false completeness claim. Do not recommend a
  complete schedule until resolved and covered by fictional tests.

## B. Quiz validation

**Proposed owner sequence after separate approval:** In a test course with synthetic quizzes,
check `/d2l/api/versions/` for LE 1.82–1.120 and LP 1.49–1.120. Check browser-session acceptance
and student-role availability of `GET /d2l/api/le/{v}/{org}/quizzes/`. Compare visible, hidden,
unreleased and unavailable synthetic quizzes with the owner's expected visibility. Confirm list
field names and types, nullable dates and flags, and whether `Objects`/`Next` indicates more
pages; the current client reports a page signal as incomplete and does not follow it. Empty
results do not prove that a course has no quizzes.

Before an attempts request, separately establish that `GET /d2l/api/lp/{v}/users/whoami` accepts
the same approved session and returns one positive numeric `Identifier` for the signed-in test
student. The owner may compare identity in memory using an authorized test-account record; never
write down the identifier. Only then consider the filtered
`GET /d2l/api/le/{v}/{org}/quizzes/{id}/attempts/?userId=...` for an owner-controlled quiz.
Verify that the filter is honored and every returned `UserId` and `QuizId` belongs to that student
and quiz. Inspect response structure and page signals without retaining attempts. Check that
hidden, unreleased, absent and denied states are not misreported as zero attempts or released
results. `IsPublished` concerns feedback availability; the CLI deliberately hides `Score` and
does not infer grade release. If `whoami` fails or its identifier does not map to `UserId`, stop:
the command must never issue an unfiltered attempts request. Use fictional fixtures for error
paths, including 401/403/404, malformed ids and owner mismatch.

**Quiz go/no-go**

- [ ] GO for listing: Student-role list access, supported LE version, accepted session, expected
  visibility, response structure and completeness behavior are confirmed.
- [ ] GO for attempts: `whoami` identifies the signed-in student, LP/LE identity equivalence is
  established, the filtered route is available, every returned attempt proves ownership, and
  unavailable or unreleased data stays undisclosed. Permission and error paths fail closed.
- [ ] NO-GO: Unknown identity mapping, ignored filter, missing or mismatched `UserId`, unexpected
  student visibility, ambiguous release semantics presented as results, or false completeness.
  Listing and attempts receive separate decisions.

## C. Discussion validation

**Proposed owner sequence after separate approval:** In an owner-controlled test course, check
the supported LE version (1.82–1.120), browser-session acceptance, student permissions, and the
field structure and course/forum associations of `GET .../discussions/forums/` and
`GET .../forums/{id}/topics/`. Check `Objects`/`Next`; the current listing parser marks `Next`
as more pages and does not fetch them. A permission denial is an error, not an empty list.

Treat posts as a separate, higher-risk decision. First seek an authoritative provider statement
or tenant administrator evidence about whether *retrieving* posts updates the signed-in student's
read state. If that is unavailable, design a separate, explicitly approved experiment using a
synthetic student, synthetic topic and a read-state observer that does not itself open the topic
or change its state. Record the before/after state as unchanged, changed or unknown without
capturing post content or account ids. Verify the observer's own side effects before using it.
Do not use a production student's unread posts as the experiment. If the GET changes read state,
the observer is unreliable, or the outcome is unknown, retain the no-go decision. This plan does
not authorize the experiment or any posts request.

The existing `d2l discussions posts` command refuses before reading credentials unless
`--acknowledge-read-state-risk` is supplied for that invocation, and warns when it is. That flag
acknowledges uncertainty; it does not certify safety. Only after the read-state gate passes may
the owner separately approve a bounded compatibility check for post shape, forum/topic/thread
binding, `sort=creationdate`, `pageSize=100`, `pageNumber`, empty-page termination and the 20-page
cap. A short page is not a completion signal. No post body, author, read flag, or raw response
belongs in the evidence note.

**Discussion go/no-go**

- [ ] GO for forums/topics: Supported LE version, session and permission acceptance, expected
  structure, association and page/completeness behavior are confirmed.
- [ ] GO for recommending posts: Independent trustworthy evidence shows no server-side read-state
  change for the exact tenant, role and route; the owner separately approves the bounded test;
  structure, identity binding, pagination and error behavior then pass. Keep per-invocation
  acknowledgment and document the narrow tested scope.
- [ ] NO-GO for posts: Read-state effect is changed or unknown, the observation method can change
  state, or any compatibility or completeness check fails. Do not recommend post retrieval.

## Decision record

For content TOC and syllabus file retrieval, apply the separate version and authorization gates
in the [M1.2 TOC decision](M1_CONTENT_COMPATIBILITY_DECISION.md) and
[M2 file decision](M2_MODERN_FILE_COMPATIBILITY_DECISION.md). Modern selection is fixture-tested
only; the two routes remain independently bounded. Approved authenticated checks must establish
session acceptance and permission separately for the TOC and file routes. A valid empty TOC is
not evidence of exhaustive course content. This plan does not authorize either request.

Keep one private, redacted decision per integration and tenant type: **GO**, **limited scope** or
**NO-GO**, with date, owner, checked route templates and versions, pass/fail/unknown for each gate,
synthetic fixture references, and unresolved questions. Revalidate after a provider or client
version change. Do not turn a tenant-specific result into a general Brightspace compatibility
claim.
