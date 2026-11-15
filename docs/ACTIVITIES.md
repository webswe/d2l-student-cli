# Calendar, quizzes and discussions

Three read-only command groups built on the same GET-only client as the rest of the tool. They use
your signed-in browser session and the documented Brightspace Valence *Learning Environment* (`le`)
API. **None of them has been confirmed against a live institution**: the routes and response
shapes below come from the published Valence reference and are exercised only by offline tests on
fictional data. Treat each as unverified until you have run it against your own account.

## What all three share

- **Requests:** `GET` only, to the configured HTTPS origin, with relative paths. No request method
  other than `GET` exists in the client.
- **API version:** the newest `le` version your institution lists between 1.82 and 1.120. Every
  route used here is documented from 1.82; older versions are obsolete for them, so the command
  stops with a clear error instead of requesting a route that cannot exist.
- **Fail closed:** a response that deviates from the documented shape, names another course,
  repeats an id, or exceeds a bound is an error, never partial data presented as complete.
- **Ids:** course, event, quiz, forum, topic and post ids are validated as plain numeric ids
  before any request is built.
- **Text:** provider text is reduced to sanitized plain text; terminal control sequences, markup
  and active content cannot reach your terminal.
- **Pages:** a provider that reports more pages than were returned is marked incomplete. Absolute
  "next page" links are never followed.
- **Permissions:** a student role may not be allowed to call every route. A denied route is an
  error that says so; it is never reported as "nothing there".

## Calendar

```text
d2l calendar list --course <id> [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--json]
d2l calendar upcoming [--days N] [--all-courses] [--json]
```

- **Route:** `GET /d2l/api/le/{version}/{orgUnitId}/calendar/events/myEvents/` with
  `startDateTime` and `endDateTime` (UTC). Documented scope: `calendar:my_events:read`.
- **Window:** a half-open UTC interval `[start, end)`. `list` defaults to the next 30 days;
  `--from`/`--to` are UTC dates (the end day is excluded). `upcoming --days N` means N elapsed
  24-hour periods from now, so a daylight-saving change cannot move the window. Windows are
  limited to 366 days.
- **Timed events** carry UTC instants (`start_utc`, `end_utc`); an unknown end stays `null` and is
  never treated as midnight. In text mode they are shown in your computer's local time with the
  UTC offset, converted from the instant by the system's own zone rules.
- **All-day events** carry local calendar days (`start_day`, `end_day`, end exclusive) and no
  instant. They are compared with the window by day, and the window's partial final day counts.
- **Recurring events** are reported once with `recurring: true` and `occurrences_expanded: false`.
  Occurrence-aware routes exist from API 1.94, but their response shape could not be verified, so
  no occurrence list is requested and none is invented.
- **Ordering:** by UTC start; an all-day event sorts by its day at 00:00 UTC. That key is for
  ordering only.
- **Across courses:** one request per active accessible course. A course that cannot be read is
  listed in `courses_failed` (with a fixed reason code) and a warning goes to stderr; the result
  is marked `complete: false`. If every course fails the command fails. Events with the same id
  in more than one course are counted once (`duplicates_dropped`). Events tied to an assignment
  or quiz carry `associated_entity`, so a caller combining this with other listings can
  de-duplicate.
- **Unverified:** live response shapes, whether `EventType` is present on your version, the
  provider's own inclusive/exclusive window rule (results are re-filtered locally), and any
  tenant-specific calendar permission rules.

## Quizzes

```text
d2l quizzes list --course <id> [--json]
d2l quizzes attempts --course <id> --quiz <id> [--json]
```

- **Routes:** `GET /d2l/api/le/{version}/{orgUnitId}/quizzes/` (scope `quizzing:quizzes:read`) and
  `GET .../quizzes/{quizId}/attempts/` (scope `quizzing:attempts:read`).
- **Metadata:** name, description (only if the instructor displays it), active flag, start, end and
  due dates (UTC), an enforced time limit in minutes, and the attempts allowed (or unlimited).
  `null` always means the provider did not report a value; it is never zero. A time limit that is
  not enforced is reported as no limit, and unlimited attempts as no count.
- **Never requested:** the quiz `questions` routes (they expose answer options), attempt feedback,
  and anything that starts, answers or submits a quiz. No command can start, answer or submit a quiz.
- **Attempts:** your own attempt summaries. A score appears only when the provider marks the
  attempt released (`score_released: true`); otherwise `score` is `null` and `score_released`
  says whether the provider withheld it (`false`) or did not say (`null`). The documentation does
  not say whether a student role may call the attempts route. The CLI does not know your user id,
  so it does not filter by user; instead, if the response contains attempts from more than one
  user, the command refuses to show any of them.
- **Status** describes only what the attempt record shows: `completed` (a completion time),
  `in_progress` (a start time, no completion), or `unknown`.
- **No false claims:** an empty quiz list does not prove a course has no quizzes (Brightspace
  shows only quizzes your role may view), and an empty attempt list does not prove a quiz was
  never attempted. Neither command infers "completed" or "not attempted"; both warn on stderr.
  A permission denial is an error that says so.
- **Unverified:** live response shapes, whether students may read the attempts route, whether
  `IsPublished` is the right release signal for scores on your version, and quiz permission rules
  at your institution.

## Discussions

```text
d2l discussions forums --course <id> [--json]
d2l discussions topics --course <id> --forum <id> [--json]
d2l discussions posts --course <id> --forum <id> --topic <id> [--thread <id>] [--json]
```

- **Routes:** `GET /d2l/api/le/{version}/{orgUnitId}/discussions/forums/`, `.../forums/{forumId}/topics/`
  and `.../topics/{topicId}/posts/` (scopes `discussions:forums:readonly`,
  `discussions:topics:readonly`, `discussions:posts:readonly`). There is no separate threads route;
  `--thread` adds the documented `threadId` filter.
- **Pages:** posts are read with `pageSize=100` and `pageNumber`, up to 20 pages (2,000 posts). A
  short page ends the list; a full page asks for the next. A page larger than requested, a repeated
  post id, or a post outside the requested thread is an error. Reaching the page cap marks the
  result `complete: false`; a partial page of posts is never presented as the whole discussion.
- **Visibility:** you see only what Brightspace shows your role: hidden forums and topics, group
  restrictions and private threads are enforced by the provider and are not bypassed. A denied
  route is an error, never "no posts".
- **Privacy:** an anonymous post has no author. A deleted post keeps its place in the thread but
  has no subject, text or author. Only a display name is ever shown; no user identifier, rating,
  vote or read flag exists in the output. `requires_approval` reports moderation state.
- **Threads:** `parent_post_id` is exactly what the provider sent, and `parent_in_results` says
  whether that parent is among the posts returned, so a missing parent is visible rather than
  invented. `reply_count` comes from the provider's own reply list and is `null` when none was
  sent.
- **Text:** bodies are sanitized plain text. A body longer than 20,000 characters is cut and
  marked `body_truncated: true`; a body too large to sanitize safely is an error.
- **Read status:** the commands never request the read-status, rating, vote, flag, approval or
  attachment routes and make no request for the purpose of changing read state. The published
  reference does not say whether a plain `GET .../posts/` marks posts as read on the server, so
  that side effect is **unverified**.
- **Unverified:** live response shapes (including whether the posts route returns a bare array),
  whether `IsAnonymous`/`IsDeleted` are present on your version, server-side read marking, and
  tenant-specific discussion permissions.
