# Calendar, quizzes and discussions (experimental)

> **Experimental, read-only beta capabilities.** These three command groups have been tested
> only offline, on fictional data. **None of them has been confirmed against a live
> institution**, including Kennesaw State University, and they must not be assumed to work
> there or anywhere else just because the older commands do. They use your signed-in browser
> session and the documented Brightspace Valence *Learning Environment* (`le`) and *Platform*
> (`lp`) APIs; whether a browser-session cookie is accepted by every route below is unknown.

## What all three share

- **Requests:** `GET` only, to the configured HTTPS origin, with relative paths. No request method
  other than `GET` exists in the client, redirects are never followed, and the session cookie is
  only ever sent to the origin it was created for.
- **API versions:** the newest version your institution lists inside each route's documented
  range. `le` and `lp` are separate numbering schemes and are never mixed (see the matrix below).
  There is no version probing beyond the one `/d2l/api/versions/` request.
- **Fail closed:** a response that deviates from the documented shape, names another course,
  repeats an id, or exceeds a bound is an error, never partial data presented as complete.
- **Ids:** every id is validated as a plain numeric id before any request is built.
- **Text:** provider text is reduced to sanitized plain text; terminal control sequences, markup
  and active content cannot reach your terminal. Decoded text such as `<script>` is shown as
  inert text; treat all of it as untrusted data.
- **Completeness is explicit:** every result says whether it is complete and, when it is not,
  why. An empty result never claims that nothing exists, and a permission denial is an error.

## Route compatibility matrix (all UNVERIFIED live)

| Command | Route | API | Documented from | Scope (documented) | Open questions |
|---|---|---|---|---|---|
| all | `/d2l/api/versions/` | - | - | - | Reachable with the session? |
| `quizzes attempts` | `GET /d2l/api/lp/{v}/users/whoami` | lp | 1.49 | `users:profile:read` or `users:own_profile:read` | Session accepted? Is `Identifier` the same id as a quiz attempt's `UserId` (not documented)? |
| `calendar list/upcoming` | `GET /d2l/api/le/{v}/{org}/calendar/events/myEvents/` | le | 1.82 | `calendar:my_events:read` | Window inclusivity undocumented (results re-filtered locally) |
| `calendar list/upcoming` | `GET /d2l/api/le/{v}/calendar/events/myEventsWithOccurrences/` | le | 1.94 | `calendar:my_events:read` | `ObjectListPage` paging undefined |
| `quizzes list` | `GET /d2l/api/le/{v}/{org}/quizzes/` | le | 1.82 | `quizzing:quizzes:read` | Student visibility |
| `quizzes attempts` | `GET /d2l/api/le/{v}/{org}/quizzes/{id}/attempts/?userId=` | le | 1.82 | `quizzing:attempts:read` | May a student call it at all? |
| `discussions forums/topics` | `.../discussions/forums/`, `.../forums/{id}/topics/` | le | 1.82 | `discussions:forums:readonly`, `discussions:topics:readonly` | Paging undocumented |
| `discussions posts` | `.../topics/{id}/posts/?pageSize&pageNumber&sort&threadId` | le | 1.82 | `discussions:posts:readonly` | No total, next link or end signal documented; server-side read marking unknown |

The `le` range accepted here is 1.82 to 1.120 and the `lp` range 1.49 to 1.120. OAuth scopes
describe API-key applications; this tool does not use OAuth, so scopes are listed only to show
what each route is documented to require.

## Calendar

```text
d2l calendar list --course <id> [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--json]
d2l calendar upcoming [--days N] [--all-courses] [--json]
```

- **Window:** a half-open UTC interval `[start, end)`. `list` defaults to the next 30 days;
  `--from`/`--to` are UTC dates (the end day is excluded). `upcoming --days N` means N elapsed
  24-hour periods from now, so a daylight-saving change cannot move it. At most 366 days.
- **Timed events** carry UTC instants; an unknown end stays `null` and is never midnight. Text
  mode shows local time with its UTC offset, converted from the instant by the system's zone
  rules. A timed event is inside the window if it starts before the end and has not ended by the
  start; an event with an unknown end counts only if it starts inside.
- **All-day events** carry local calendar days (`start_day`, `end_day`, end exclusive) and never
  an instant. They are compared with the window by day; a window ending mid-day includes that day.
- **Recurring events, two modes:**
  - On LE 1.94 or later the documented occurrence route is used. Each occurrence of a recurring
    event is its own record (`occurrence_id` set, `occurrences_expanded: true`) with the
    occurrence's own start and end; an empty occurrence list means the provider reports none in
    the window; nothing is invented. If that route is refused (403) or absent (404) the command
    falls back exactly once to the base route for that course and records it
    (`occurrence_route_fallback_courses`).
  - Otherwise (older versions, or after a fallback) a recurring event appears once as a *series
    definition* (`occurrences_expanded: false`, `occurrence_id: null`); its start and end describe
    the series, not every occurrence.
- **Completeness:** `complete` is `false` whenever a course failed, the provider reported more
  pages (never followed), or any recurring event is only a definition. `incomplete_reasons` lists
  `course_failed`, `more_pages` and/or `recurring_not_expanded`, and stderr warns. A schedule
  containing unexpanded recurring events is never presented as complete.
- **Across courses:** one request per active accessible course. A failing course is named with a
  fixed reason code; if every course fails the command fails; an expired session aborts.
  Events are de-duplicated by `(event id, occurrence id)`, so distinct occurrences are never
  collapsed. Events tied to an assignment or quiz carry `associated_entity`.
- **Unverified:** everything in the matrix, `EventType` availability, and the exact occurrence
  response shape on a real tenant.

## Quizzes

```text
d2l quizzes list --course <id> [--json]
d2l quizzes attempts --course <id> --quiz <id> [--json]
```

- **Listing:** name, description (only if the instructor displays it), active flag, start, end and
  due dates (UTC), an enforced time limit and the attempts allowed. `null` means not reported,
  never zero. No question, answer or feedback route is ever requested.
- **Attempt ownership (verified identity):** the attempts route returns attempts "for the quiz
  generally" unless filtered, so the command first calls `whoami` with the same session and
  origin, requires one positive numeric `Identifier`, requests attempts with `userId=<that id>`,
  and then requires every returned record to carry exactly that `UserId` and the requested
  `QuizId`. Nothing else is trusted: not a flag, a file, a roster, the first attempt, or an
  earlier session (the id lives in memory for one command). If `whoami` is unavailable (401, 403,
  404, malformed, an id of zero) the attempts request is never made, there is no unfiltered
  fallback, and the command fails with exit 1 and no data. An attempt belonging to another user,
  without a `UserId`, or for another quiz rejects the whole response. If the provider's
  `Identifier` is not the same id as attempts' `UserId` the command refuses rather than guesses.
- **Release semantics:** `IsPublished` is documented only as "the attempt feedback is available
  to the student". It is reported as `feedback_published` (`true`, `false`, `null` unknown). The
  numeric `Score` has no documented meaning or release rule, so it is validated and **never
  shown**: `score` and `score_released` are always `null` (not established, not zero). Nothing
  equates a completed attempt with a released grade. `attempt_completed` and `status` reflect
  only the attempt's own start and completion times.
- **No false claims:** an empty quiz list does not prove a course has no quizzes, and an empty
  attempt list does not prove a quiz was never attempted; both warn on stderr.
- **Unverified:** that a student session may read `whoami` or the attempts route, the identifier
  equivalence above, and the score release rule.

## Discussions

```text
d2l discussions forums --course <id> [--json]
d2l discussions topics --course <id> --forum <id> [--json]
d2l discussions posts --course <id> --forum <id> --topic <id> [--thread <id>] [--json]
```

- **Pagination:** the posts route documents `pageSize` (max 1000), `pageNumber`, `sort` and
  `threadId`, and returns a plain array; it documents no total, no next-page link and no
  end-of-data signal, and the default order is newest first (unstable while posts arrive). The
  command therefore sends an explicit ascending `sort=creationdate`, reads pages of 100, and
  treats the result as **complete only after an empty page is observed**. A short page is not an
  end signal: reading continues. `completeness_basis` says why a read stopped:
  `empty_page_observed` (complete), `page_cap_reached` (20 pages = 2,000 posts),
  `size_cap_reached` (5,000,000 characters), or `end_not_confirmed` (a later page failed or was
  refused: the posts already read are kept). Pages beyond a cap are never requested; a first-page
  failure is an error, not an empty topic.
- **Safety checks:** a page larger than requested, a repeated post id (including repeated or
  reordered pages), a post from another topic or forum, a post outside the requested thread, or a
  provider cursor are errors; no cursor is ever followed.
- **Visibility:** you see only what Brightspace shows your role. A denied route is an error.
- **Privacy:** an anonymous post has no author; a deleted post keeps its place with no text or
  author; only a display name is ever shown, never a user id, rating, vote or read flag.
- **Threads:** `parent_post_id` is as sent, and `parent_in_results` says whether that parent was
  returned, so a missing parent is visible, not invented. `reply_count` comes from the provider's
  own list and is `null` when none was sent.
- **Text:** sanitized plain text, cut at 20,000 characters with `body_truncated: true`.
- **Read status:** no read-status, rating, vote, flag, approval or attachment route is requested.
  Whether a plain posts `GET` marks posts read server-side is not documented and is unverified.
- **Unverified:** everything in the matrix, anonymity and deletion fields on a real tenant, and
  what the provider returns for a page past the end (documented nowhere).
