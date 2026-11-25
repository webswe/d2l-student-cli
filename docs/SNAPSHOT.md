# Academic snapshot JSON contract v1

`d2l snapshot --json` composes the existing course list and course-scoped
assignment, displayed-grade, and announcement services. It introduces no
provider route, file retrieval, grade calculation, or persistent cache.
`overview --json` retains its existing contract. All example values below are
fictional.

```text
d2l snapshot --json [--sources assignments,grades,announcements] [--max-courses N] [--all-courses]
```

The default sources are all three, in the canonical order shown. `--sources`
accepts a nonempty, comma-separated subset without repeats. The default
course limit is 10; the allowed range is 1 through 20. The command lists
courses once and selects accessible active courses by default. `--all-courses`
also selects inactive accessible courses. If selected courses exceed the limit,
the command exits 1 before making per-course reads; increase the explicit
limit or use the existing single-course commands. The course list must have
unique, ASCII decimal IDs. An invalid or repeated course ID aborts the snapshot.

On successful reads, stdout is one UTF-8 JSON object followed by a newline.
The top-level fields in schema version 1 are exactly:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | Always `1`. Reject unknown versions. |
| `generated_at` | string | UTC ISO 8601 time when the in-memory result was assembled; not a provider timestamp. |
| `requested_sources` | string array | Canonical order of requested course-scoped sources. |
| `successful_sources` | string array | Requested sources that succeeded for **every selected course**. An empty selected course set is vacuously successful. |
| `partial` | boolean | True when at least one requested course/source read failed. It does not mean complete academic coverage when false. |
| `service_calls` | integer | One course-list invocation plus one invocation per requested source and selected course. This is not an HTTP count. |
| `http_request_count` | null | The current services do not expose accurate transport accounting. |
| `courses` | array | Selected courses sorted by numeric-ID **string** order, then code and name. |

Each `courses` entry has exactly `course`, `sources`, `assignments`,
`grade_items`, and `announcements`. `course` has the same normalized fields as
`courses list --json`. `sources` always contains the three keys `assignments`,
`grades`, and `announcements`. Each value has exactly `status`, `completeness`,
`error_code`, `observed_row_count`, and `page_size_ceiling`.

`status` is `success`, `failed`, or `not_requested`. A successful empty source
has an empty record array and `success`. A failed or unrequested source also
has an empty array but is distinguished by its status. A failed source has a
stable error class name in `error_code` or `InvalidNormalizedData`; its raw
exception message, provider body, URL, and academic text are never included.
Other status fields are null on failure or when unrequested. `completeness`
preserves the existing source result: assignments use
`potentially_truncated`, `below_page_size_boundary`, or `unknown`; grades
and announcements currently report `unknown`. Assignment row count and page
ceiling are provided only when the source succeeded. Even
`below_page_size_boundary` is evidence about one page boundary, not proof of
all assignments. No snapshot asserts total completeness.

The record arrays reuse the existing command serializers. Assignment identity
is `(course_id, id)`; announcements use `(course_id, announcement_id)`. Exact
duplicates are removed. A conflicting duplicate identity fails that source
for its course rather than choosing an arbitrary value. Grade items have no
verified provider item ID; exact duplicate rows are removed, and distinct
rows are retained without fabricated IDs. Records are sorted deterministically
by identity or displayed fields. Assignment dates retain their naive displayed
meaning. Grade values remain display strings; no official final grade is
calculated. Announcement `body` is existing sanitized text. All academic text
must still be treated as untrusted data by consumers.

An example with one empty source and one unrequested source:

```json
{
  "schema_version": 1,
  "generated_at": "2026-01-01T12:00:00+00:00",
  "requested_sources": ["assignments", "grades"],
  "successful_sources": ["assignments", "grades"],
  "partial": false,
  "service_calls": 3,
  "http_request_count": null,
  "courses": [{
    "course": {"id": "10", "code": "DEMO10", "name": "Fictional Course", "semester_name": "Fall", "is_active": true, "can_access": true, "is_new_enrollment": false, "start_date": null, "end_date": null},
    "sources": {
      "assignments": {"status": "success", "completeness": "unknown", "error_code": null, "observed_row_count": 0, "page_size_ceiling": null},
      "grades": {"status": "success", "completeness": "unknown", "error_code": null, "observed_row_count": null, "page_size_ceiling": null},
      "announcements": {"status": "not_requested", "completeness": null, "error_code": null, "observed_row_count": null, "page_size_ceiling": null}
    },
    "assignments": [], "grade_items": [], "announcements": []
  }]
}
```

Exit 0 means all requested reads succeeded or there was at least one success
alongside failures. Partial success emits a fixed warning to stderr and must
be checked through `partial` and each source status. If all requested
course/source reads fail, the command still emits the structured envelope and
exits 1. Authentication expiration and rate limiting abort the operation with
empty stdout and exit 1; they are never represented as an empty successful
course. Unsupported source syntax exits 2 before authentication. Other scope
or provider errors exit 1 with empty stdout. No result is persisted by the
command. Subprocess callers must check exit status and schema version, parse
the envelope even when the status is 1 and stdout is nonempty, and distinguish
partial evidence from a complete set of returned reads.
