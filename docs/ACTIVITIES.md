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
