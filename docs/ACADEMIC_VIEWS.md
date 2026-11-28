# Cross-course academic views

Four read-only commands present data the existing services already return, across
several courses at once. They add **no provider route**, no file retrieval, no
cache, no background work, and no write. Each is one `SnapshotService`
invocation: one course-list read, then one existing read per selected course and
source. The existing `snapshot` and `overview` contracts are unchanged. All
example values are fictional.

| Command | Source reads per course | Evidence class |
| --- | --- | --- |
| `d2l grades overview` | grades | Implemented, offline-tested; provider behavior as for `grades list` |
| `d2l assignments dashboard` | assignments | Implemented, offline-tested; provider behavior as for `assignments list` |
| `d2l announcements overview` | announcements | Implemented, offline-tested; provider behavior as for `announcements list` |
| `d2l student overview` | assignments, grades, announcements | Implemented, offline-tested |

None of these has been run against a live provider. Offline tests do not raise
the evidence class of the underlying readers.

## Shared options

```text
--json                  versioned JSON envelope on stdout
--format text|markdown  human report (default text); cannot be combined with --json
--course QUERY          repeatable; id, code or name resolved by the existing course resolver
--max-courses N         1-20, default 10
--all-courses           include inactive accessible courses (ignored when --course is given:
                        the resolver only returns active, accessible courses)
```

All options are validated before credentials or the network are touched
(exit 2). `--course` queries use the same deterministic resolver as
`courses resolve`: an unknown, inactive, inaccessible or **ambiguous** query
fails with exit 1 after the single course-list read and **before any per-course
read**; nothing is silently picked. More than `--max-courses` selected courses
also fails before per-course reads.

## Exit codes and streams

* stdout carries only the report or JSON object.
* Warnings and errors go to stderr.
* `0` – at least one selected source read succeeded, or no course was selected.
  If some reads failed the envelope has `partial: true` and stderr carries a
  warning.
* `1` – authentication/provider/selection error, or **every** selected read failed.
* `2` – invalid options.

A failed read is never rendered as an empty list: it has `source.status:
"failed"`, a null count, and an empty record array. A successful read with zero
rows has `status: "success"` and count `0`; even then, provider totals are
unverified.

## Common envelope fields

| Field | Meaning |
| --- | --- |
| `schema_version` | `1`. Reject unknown versions. |
| `kind` | `grades_overview`, `assignments_dashboard`, `announcements_overview`, `student_overview` |
| `generated_at` | UTC time the in-memory result was assembled (not a provider time) |
| `partial` | at least one selected course read of a relevant source failed |
| `provider_totals_verified` | always `false` |
| `course_count` | selected courses |
| `courses[].course` | `{id, code, name}` |
| `courses[].source` | `{status: success|failed, completeness, error_code}`; `error_code` is a stable class name, never a provider message |

Courses are ordered by numeric-ID string order (existing snapshot behavior).
Within a course, records keep **provider order**; rows are never collapsed.

## `grades overview`

Adds `official_final_grade_calculated: false` and
`item_identity: "positional_provider_order"`. Each course has
`grade_item_count` (null on failure) and `grade_items[]` with `index`
(position in the provider listing), `label`, `displayed_points`,
`displayed_grade`, `displayed_weight`, `feedback_availability`. Blank cells are
`null`, never zero. Grade rows have no provider identity, so identical rows are
all retained and `index` is positional only. No average, percentage, or final
grade is computed.

## `assignments dashboard`

Extra options: `--sort provider|due` (default `provider`) and
`--due-date any|recognized|unrecognized`.

`items[]` is flattened across courses with `course`, `index`, `id`, `name`,
`due_date` (ISO 8601 without offset, or null), `due_date_recognized`,
`submission_status`, `score` (all provider display strings). `courses[]` adds
`observed_row_count`, `page_size_ceiling` and `assignment_count`.
`observed_assignment_count` is the unfiltered total; `returned_item_count` is
after `--due-date`.

* `due_date` is the provider's displayed wall-clock time with no verified
  timezone. `null` means none was **recognized**; it is never labeled
  "no deadline".
* No overdue, due-today, or completed classification is made.
* `--sort due` orders recognized dates ascending; ties and unrecognized dates
  keep course/provider order, with unrecognized dates last.

## `announcements overview`

Extra option: `--items-per-course N` (1-100, default 25).

Adds `items_per_course_limit`, `ordering: "course_id_then_provider_order"` and
`chronological_order_verified: false`. The listing carries no verified
timestamp, so the feed is **not** newest-first. Each course has
`observed_announcement_count`, `omitted_by_item_limit`, and `announcements[]`
with `index`, `announcement_id`, sanitized `title`, `body_preview` (single line,
at most 300 characters) and `body_preview_truncated`. The provider
`detail_reference` is deliberately not exposed, no detail page is followed, and
nothing is marked read.

## `student overview`

Adds `counts_are_observed_only: true`, per-source `sources.<name>.courses_succeeded`
/ `courses_failed`, per-course observed counts (null where that source failed),
and `totals`. Totals sum only successful sources and are **observed counts, not
provider totals**. It reuses the snapshot read set, so no duplicate calls occur
(`1 + sources x courses` service invocations).

## Human output safety

Text and Markdown output treat provider text as untrusted: ANSI/OSC/control
characters are stripped, text is flattened to one line, bounded, URL-like tokens
are replaced with `[URL omitted]`, and Markdown output additionally escapes
Markdown and HTML. Rendering never contacts the provider. JSON retains
provider strings as returned by the normalized models (announcement titles and
previews are additionally sanitized); machine consumers must still treat all
strings as data, never instructions.

## Not provided (evidence-gated)

Official final grades or course averages, grade feedback text, assignment
details, overdue/due-soon/completed classification, announcement timestamps or
unread counts, and any file download.
