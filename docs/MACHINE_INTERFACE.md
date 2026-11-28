# Machine-readable interface

The additive `snapshot` v1 envelope is specified in [SNAPSHOT.md](SNAPSHOT.md). The four cross-course
view envelopes (`grades_overview`, `assignments_dashboard`, `announcements_overview`,
`student_overview`, each `schema_version` 1) are specified in [ACADEMIC_VIEWS.md](ACADEMIC_VIEWS.md).
Existing commands and envelopes retain their contracts.
The additive, offline `diagnostics --json` command is specified in
[DIAGNOSTICS.md](DIAGNOSTICS.md). It never reads a saved session or contacts a
provider.

This document defines the supported subprocess JSON interface of D2L-CLI 0.1.0.
It describes normalized
output, not Brightspace's undocumented responses. Commands and fields outside
this document are not part of the machine interface.

The supported JSON commands are:

```text
d2l courses list --json
d2l assignments list --course <id> --json [--include-completeness]
d2l assignments overview --json [--chronological] [--all-courses] [--include-completeness]
d2l grades list --course <id> --json [--include-completeness]
d2l announcements list --course <id> --json [--include-completeness]
d2l overview --json [--all-courses]
d2l snapshot --json [--sources assignments,grades,announcements] [--max-courses N] [--all-courses]
d2l grades overview --json [--course Q]... [--max-courses N] [--all-courses]
d2l assignments dashboard --json [--sort provider|due] [--due-date any|recognized|unrecognized] [...]
d2l announcements overview --json [--items-per-course N] [...]
d2l student overview --json [...]
d2l syllabus find --course <id> --json
d2l syllabus fetch --course <id> --topic <topicId> --json
```

The two `syllabus` commands are specified in `docs/SYLLABUS_DISCOVERY.md` (their JSON objects are
`syllabus_search` and `syllabus_document`).

From a repository checkout, replace `d2l` with `uv run d2l`. The `--details`
option affects only human-readable output; JSON always contains the complete
normalized records retrieved by the command.

## Common process contract

- Exit `0` means the command completed successfully. An empty result is a
  successful JSON document containing an empty array.
- Anticipated authentication, secure-storage, provider, validation, and
  provider-structure failures exit `1`. Their stdout is empty and a safe,
  human-readable message beginning with `error:` is written to stderr.
- Command-line usage errors, such as a missing required `--course`, exit `2`
  and do not emit JSON.
- On success, stdout contains one JSON object followed by a newline. Diagnostic
  messages never share stdout. Consumers must check the exit code before
  parsing stdout.
- Warnings use stderr. Assignment and unified-overview completeness warnings
  can accompany successful JSON. Warning text is diagnostic, not a structured
  API, and must not be parsed. Supported completeness values are in the JSON
  fields below.
- JSON strings round-trip Unicode. The current encoder may use JSON `\uXXXX`
  escapes, which are semantically equivalent to literal Unicode and keep the
  stream safe to decode as UTF-8.
- Object member order is not a contract. Array order is a contract as described
  for each command.
- Diagnostics omit credentials, authenticated URLs, course identifiers,
  response bodies, and academic record values. Applications should still treat
  stderr as private operational data.

Authentication is a prerequisite. The user must first run `d2l auth login` and
complete institutional SSO/MFA interactively. External applications must not
automate that browser interaction or handle a password or MFA code. `d2l auth
status` is human-readable and makes one verification request; it has no JSON mode.

The stored session is bound to the origin it was created for. If the configured base URL
(`D2L_CLI_BASE_URL`) is a different origin, or the stored record has no recorded origin, every
command exits with status 1 and an `error:` line on stderr asking the user to log in again,
stdout stays empty, and no request is sent to any site.

## Shared record schemas

Every listed key is required in its record. `null` is a value of a required,
nullable field; it does not mean that the key can be omitted.

### Course

| Key | JSON type | Meaning |
|---|---|---|
| `id` | ASCII decimal-digit string | Brightspace course/org-unit identifier. |
| `code` | string | Provider-displayed course code. |
| `name` | string | Provider-displayed course name. |
| `semester_name` | string | Provider-displayed semester name. |
| `is_active` | boolean | Provider active flag. |
| `can_access` | boolean | Provider access flag. |
| `is_new_enrollment` | boolean | Provider new-enrollment flag. |
| `start_date` | string or null | Unparsed provider date string. |
| `end_date` | string or null | Unparsed provider date string. |

`id` is the course join key within one configured Brightspace origin. Course
identifiers and every `course_id` association below contain ASCII digits only.
No cross-origin identity guarantee is made.

### Assignment

| Key | JSON type | Meaning |
|---|---|---|
| `id` | string | Assignment/Dropbox identifier within the course. |
| `course_id` | ASCII decimal-digit string | Owning course/org-unit identifier. |
| `name` | string | Provider-displayed assignment name. |
| `submission_status` | string or null | Uninterpreted display text. |
| `score` | string or null | Uninterpreted display text. |
| `due_date` | string or null | Timezone-naive ISO-8601 display value. |

Assignment identity is the pair (`course_id`, `id`), not `id` alone. A null
`due_date` means no positively recognized due date was present; it does not
mean that the assignment has no deadline.

### Assignment completeness

| Key | JSON type | Meaning |
|---|---|---|
| `status` | string enum | `below_page_size_boundary`, `potentially_truncated`, or `unknown`. |
| `observed_row_count` | integer | Assignment-shaped rows observed before normalization. |
| `page_size_ceiling` | integer or null | Selected page size when reliably observed. |

`below_page_size_boundary` is evidence about one rendered page, not a claim
that Brightspace has no other records. `potentially_truncated` means the
observed count reached the selected boundary. `unknown` means the boundary
could not be assessed.

### Grade item

| Key | JSON type | Meaning |
|---|---|---|
| `course_id` | ASCII decimal-digit string | Owning course/org-unit identifier. |
| `label` | string | Provider-displayed grade-item label. |
| `displayed_points` | string or null | Exact display text; never calculated. |
| `displayed_grade` | string or null | Exact display text; never calculated. |
| `displayed_weight` | string or null | Exact display text; never calculated. |
| `feedback_availability` | string enum | `none`, `present`, or `unknown`. |

The verified page exposes no stable grade-item identifier. Labels are not
promised unique, so consumers must not construct a durable record identity
from `label` or array position.

### Announcement

| Key | JSON type | Meaning |
|---|---|---|
| `course_id` | ASCII decimal-digit string | Owning course/org-unit identifier. |
| `announcement_id` | string | Announcement identifier within the course. |
| `title` | string | Sanitized plain-text title. |
| `body` | string or null | Sanitized plain-text body. |
| `detail_reference` | string | Validated relative reference, returned as data and never fetched. |

Announcement identity is the pair (`course_id`, `announcement_id`).
`detail_reference` is not an identity guarantee.

`body` is the complete sanitized plain text carried by the established News
listing response for that returned record, subject to the fail-closed parser
bounds below. It is not the 120-character human preview. A null body means that
no readable text remained after sanitization; it does not mean a preview was
withheld. Body availability and list completeness are separate facts.

## Command envelopes

### Course resolution and content hierarchy (new; execution validation pending)

`d2l courses resolve <query> --json` returns `{"course": COURSE}`. It makes one
course-list GET. Exact numeric ID precedes normalized exact code and name,
then unambiguous prefix and substring. Only active accessible courses qualify.
Ambiguity or absence exits 1 with empty stdout; existing numeric `--course`
options are unchanged.

`d2l content tree --course <id> --json` (alias `content toc`) returns
`{"content_tree": {"course_id": string, "completeness": "unknown",
"node_count": integer, "modules": [NODE, ...]}}`. Every node has `id`,
`title`, `parent_module_id` (string or null), `kind`, and `children` (array).
Module `kind` is `module`; a topic's kind is its sanitized provider type.
Top-level modules have a null parent. Topic and nested-module children retain
their structural parent and order within the provider's separate Topics and
Modules arrays; topics appear before nested modules. Hidden and broken content
is omitted. An empty array means no visible modules were returned, with
completeness still unknown. Titles and kinds are plain text, not trusted
instructions. One versions GET and one TOC GET are made per invocation; there
is no file GET. The content route and browser-session compatibility have not
been live validated. Parsing bounds depth to 10 and visible nodes to 10,000;
the 10,000-node traversal bound also counts hidden entries. Duplicate IDs,
cycles, missing child arrays, mismatched course associations, unsupported
top-level topics and malformed structures fail closed.

### Course listing

`d2l courses list --json` returns:

```text
{"courses": [COURSE, ...]}
```

`courses` is required and preserves provider order. A provider bookmark that
would imply another page is a failure rather than a partial success.

### Assignment listing

`d2l assignments list --course <id> --json` returns:

```text
{"assignments": [ASSIGNMENT, ...]}
```

Records preserve provider order. With `--include-completeness`, the only
top-level addition is the required-for-that-mode `completeness` object:

```text
{
  "assignments": [ASSIGNMENT, ...],
  "completeness": ASSIGNMENT_COMPLETENESS
}
```

### Cross-course assignment overview

`d2l assignments overview --json` returns:

```text
{
  "assignments": [
    {
      "id": "string",
      "course_id": "ASCII decimal-digit string",
      "course_code": "string",
      "course_name": "string",
      "name": "string",
      "due_date": "timezone-naive ISO-8601 string or null"
    }
  ]
}
```

The overview intentionally uses this reduced assignment record; it does not
include `submission_status` or `score`. By default, courses remain in provider
order and each course's assignments remain in provider order. `--chronological`
globally sorts recognized due dates earliest first, puts null dates last, and
preserves original order for ties. `--all-courses` includes inactive accessible
courses; inaccessible courses are always excluded.

With `--include-completeness`, `course_completeness` is also required and has
one record per selected course, including selected courses with no assignments:

```text
{
  "assignments": [],
  "course_completeness": [
    {
      "course_id": "ASCII decimal-digit string",
      "course_code": "string",
      "status": "below_page_size_boundary | potentially_truncated | unknown",
      "observed_row_count": 0,
      "page_size_ceiling": "integer or null"
    }
  ]
}
```

The (`course_id`, `id`) pair remains the assignment identity.

### Grade listing

`d2l grades list --course <id> --json` returns:

```text
{"grade_items": [GRADE_ITEM, ...]}
```

Items preserve provider order. With `--include-completeness`, the envelope also
contains `"completeness": "unknown"`. No verified total, page-size boundary,
or end marker exists, so `unknown` neither claims completeness nor observed
truncation.

### Announcement listing

`d2l announcements list --course <id> --json` returns:

```text
{"announcements": [ANNOUNCEMENT, ...]}
```

Announcements preserve provider order. `--details` does not change JSON. With
`--include-completeness`, the envelope also contains `"completeness":
"unknown"`, with the same evidence-bounded meaning as grade completeness.
The default human command shortens each body only for terminal presentation;
`--details`, default JSON, and JSON combined with `--details` all use the same
complete normalized `body` value. The command never follows
`detail_reference` or loads embedded resources.

### Unified academic overview

`d2l overview --json` returns `{"courses": [...]}`. Each array entry contains
exactly these required keys:

| Key | JSON type |
|---|---|
| `course` | Course object |
| `retrieved_assignment_count` | integer |
| `recognized_due_date_count` | integer |
| `without_recognized_due_date_count` | integer |
| `visible_grade_item_count` | integer |
| `assignment_completeness` | Assignment completeness object |
| `grade_completeness` | string enum; currently `unknown` |
| `assignments` | array of Assignment objects |
| `grade_items` | array of Grade item objects |
| `retrieved_announcement_count` | integer |
| `announcement_completeness` | string enum; currently `unknown` |
| `announcements` | array of Announcement objects |

Counts equal their corresponding returned arrays; the two due-date counts
partition `assignments`. Course order and each nested provider order are
preserved. Filtering matches the assignment overview. Any read or association
failure aborts the entire command: the interface never returns a silently
partial unified overview.

### Calendar, quiz and discussion commands (experimental)

These commands are **experimental**: offline-tested only and not confirmed against any live
institution. Their JSON may change before a stable release. Details, limits and the route
compatibility matrix are in [ACTIVITIES.md](ACTIVITIES.md). In every case a hard failure exits
with status 1 and empty stdout; completeness is explicit and never implied.

#### Calendar events

`d2l calendar list --course <id> --json` and `d2l calendar upcoming --json` print
`{"calendar": {...}}`.

| Field | Type | Meaning |
|---|---|---|
| `course_id` | string | Present for `list` only. |
| `window` | object | `start` and `end`, UTC `YYYY-MM-DDTHH:MM:SSZ`; half-open `[start, end)`. |
| `complete` | boolean | `false` when a course failed, more pages exist, or a recurring event is only a definition. Never ignore it. |
| `incomplete_reasons` | array of string | Any of `course_failed`, `more_pages`, `recurring_not_expanded`. |
| `recurrence` | object | `mode` (`occurrences`, `definitions_only`, `mixed`), `unexpanded_recurring_events` (integer), `occurrence_route_fallback_courses` (array). |
| `courses_requested` | integer | Courses queried. |
| `courses_failed` | array | `{course_id, reason}`; `reason` is `permission_denied`, `rate_limited`, `network_unavailable`, `unsupported_response`, `provider_error` or `error`. |
| `truncated_courses` | array of string | Courses where the provider reported more pages than were returned. |
| `duplicates_dropped` | integer | Repeated `(course id, event id, occurrence id)` counted once. |
| `events` | array | Event records ordered by start. |

Event record (every key present; `null` when unknown): `event_id`, `course_id`, `course_name`,
`course_code`, `title`, `description`, `all_day`, `start_utc` and `end_utc` (timed events;
`end_utc` may be `null`), `start_day` and `end_day` (all-day, `YYYY-MM-DD`, end exclusive),
`recurring`, `occurrence_id` (set for an expanded occurrence), `occurrences_expanded`,
`event_type`, `associated_entity` (`{type, id}` or `null`), `view_reference` (a relative path,
never fetched). A recurring event with `occurrences_expanded: false` describes the series only.

#### Quizzes

`d2l quizzes list --course <id> --json` prints `{"quizzes": {"course_id", "complete",
"quizzes": [...]}}`; `d2l quizzes attempts --course <id> --quiz <id> --json` prints
`{"quiz_attempts": {"course_id", "quiz_id", "complete", "attempts_returned", "attempts": [...]}}`.

Quiz record: `quiz_id`, `course_id`, `name`, `description`, `active`, `start_date`, `end_date`,
`due_date` (UTC), `time_limit_minutes`, `time_limit_enforced`, `attempts_allowed`,
`attempts_unlimited` (`null` = not reported).

Attempt record: `attempt_id`, `quiz_id`, `attempt_number`, `started`, `completed` (UTC), `status`
(`completed`, `in_progress`, `unknown`), `attempt_completed` (boolean), `feedback_published`
(`true`, `false` or `null` = unknown), `score_released` and `score` (**always `null`**: the
numeric score's release rule is undocumented, so it is never shown). Attempts are shown only
after the signed-in user's identity is verified (`whoami`) and every record is proven to belong to
that user and quiz; otherwise the command fails with no data. An empty `attempts` array does not
mean the quiz was not attempted. No question, answer, feedback or user-identifier field exists.

#### Discussions

`posts` requires `--acknowledge-read-state-risk` on every invocation. Without it, the command
exits 1 with empty stdout before configuration, credentials or network access. With it, a warning
about unverified server-side read-state effects goes to stderr, including in `--json` mode. The
flag does not establish that retrieval is safe.

`d2l discussions forums|topics|posts ... --json` print `{"discussion_forums": {...}}`,
`{"discussion_topics": {...}}` and `{"discussion_posts": {...}}`, each with `course_id`, a boolean
`complete` and its array. `posts` also carries `forum_id`, `topic_id`, `thread_id` (or `null`),
`completeness_basis` (`empty_page_observed` is the only complete value; others are
`page_cap_reached`, `size_cap_reached`, `end_not_confirmed`), `page_size` and `pages_fetched`.
`topics` also has `forum_id`.

- Forum: `forum_id`, `course_id`, `name`, `description`.
- Topic: `topic_id`, `forum_id`, `course_id`, `name`, `description`.
- Post: `post_id`, `course_id`, `forum_id`, `topic_id`, `thread_id`, `parent_post_id`,
  `parent_in_results` (`null` for a first post, else whether the parent was returned), `author`
  (display name; `null` when anonymous or deleted), `anonymous`, `subject`, `body` (sanitized
  text; `null` when deleted), `body_truncated`, `posted`, `edited` (UTC), `deleted`,
  `requires_approval`, `reply_count` (`null` when the provider sent no reply list).

No user identifier, read status, rating or vote field exists. A result that is not complete exits
0 with `complete: false` and a warning on stderr; a hard failure exits 1.

## Safe subprocess integration

PowerShell should keep the streams separate and test `$LASTEXITCODE` before
calling `ConvertFrom-Json`:

```powershell
$stderrPath = Join-Path $env:TEMP "d2l-cli-stderr.txt"
$jsonLines = & d2l overview --json 2> $stderrPath
$d2lExit = $LASTEXITCODE

if ($d2lExit -ne 0) {
    $diagnostic = Get-Content -Raw $stderrPath
    throw "D2L-CLI failed with exit $d2lExit`: $diagnostic"
}

$overview = $jsonLines | ConvertFrom-Json
$overview.courses.Count
```

Do not invoke through a shell from application code. Pass an argument vector,
capture stdout and stderr separately, and parse only after success. For example:

```python
import json
import subprocess

completed = subprocess.run(
    ["d2l", "courses", "list", "--json"],
    check=False,
    capture_output=True,
    encoding="utf-8",
)
if completed.returncode != 0:
    raise RuntimeError(f"d2l failed with exit {completed.returncode}")
courses = json.loads(completed.stdout)["courses"]
```

Treat returned data and any saved JSON as private academic records. Do not log
stdout or copy it into exception messages.

All academic strings are untrusted provider data. Announcement title/body
fields have been converted to safe plain text, and human-facing CLI output is
terminal-sanitized, but neither property is prompt-injection protection.
Consumers must not render fields as HTML, execute embedded text or URLs, pass
them to a shell, or treat their contents as instructions. In particular,
AI-enabled consumers remain responsible for isolating tool
output from trusted instructions.

D2L-CLI requests identity content encoding and bounds each HTTP response before
parsing it. A compressed response or a response over that safety limit is a
failure (nonzero exit and empty JSON stdout), never a partial-success document.
Consumers should also set a subprocess timeout and a reasonable captured-output
limit because a multi-course overview can make several bounded requests and
valid academic output size is not itself an API guarantee.

### Output-size characteristics

- Every HTTP response is limited to 25,000,000 identity-encoded bytes before a
  provider parser receives it.
- An announcement listing is additionally limited to 5,000,000 decoded
  characters. Each embedded announcement body is limited to 200,000 HTML input
  characters and 100,000 sanitized plain-text characters. Exceeding a bound
  fails the command; no prefix or partial JSON document is emitted.
- The CLI does not silently shorten machine-readable academic strings. JSON
  escaping and record aggregation mean stdout size is not equal to source text
  length. Cross-course overview output can be larger than any individual
  provider response.
- A consumer capture limit is therefore an independent safety boundary. If a
  complete successful document exceeds that boundary, the consumer should
  reject the invocation as oversized rather than parse the captured prefix.

## Compatibility expectations

The regression suite pins envelopes, required record keys, nullable values,
empty results, ordering, completeness states, course associations, Unicode
round trips, stream separation, and exit behavior using fictional fixtures.
It performs no live Brightspace requests.

Within the 0.1 release-candidate line, fields will not be removed, renamed, or
given a different type or meaning without an explicit compatibility decision.
Completeness fields remain opt-in where documented. New fields are not added
silently: the exact-key contract tests force a deliberate review first.
Consumers should still reject unsupported types and enum values cleanly and
should not infer undocumented semantics from strings or array positions.
