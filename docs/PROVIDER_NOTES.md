# Provider notes

What the Brightspace providers assume, and why they fail closed. This is a summary for
contributors; it is not a description of D2L's documented or supported interfaces. Brightspace
deployments differ by institution and version, and everything here may change without notice.

Section labels and references quoted in source comments (for example "Phase 2A" or "the Phase 1
report") are historical labels from the original research notes, which are not part of this
repository. Rely on the code and its tests.

## Principles

- **GET only, one origin.** Every request is a GET to the configured HTTPS origin; absolute and
  scheme-relative targets, userinfo, other ports and redirects are refused.
- **Untrusted content.** Responses are bounded (25 MB hard ceiling, tighter per endpoint), decoded
  as identity-encoded text or JSON, and parsed with strict, fail-closed parsers. An unfamiliar
  shape raises a fixed error; it is never silently treated as an empty list.
- **Identity validation.** Course, assignment and announcement identifiers must be numeric, and a
  record must belong to the course that was requested.
- **No academic data in diagnostics.** Errors and logs never contain URLs with identifiers,
  response bodies, cookies or academic text.

## Routes used

| Area | Route (relative to the configured origin) | Format |
|---|---|---|
| Courses | `/d2l/le/manageCourses/api/mycourses` | JSON |
| Assignments | `/d2l/lms/dropbox/user/folders_list.d2l` | server-rendered HTML |
| Grades | `/d2l/lms/grades/my_grades/main.d2l` (and the grades index redirect) | server-rendered HTML |
| Announcements | `/d2l/lms/news/main.d2l`, with `/d2l/le/news/<course>/<item>/view` detail links | server-rendered HTML |
| Syllabus | `/d2l/api/versions/`, then `/d2l/api/le/<tocVersion>/<course>/content/toc`; explicit fetch may use a separate `<fileVersion>` for the topic file route | JSON, then bounded file bytes only on fetch |
| Content hierarchy | Same versions and course `content/toc` GET as syllabus discovery | JSON only; browser-session compatibility unverified |

## Known limits

- TOC reads and explicit syllabus file retrieval each select the newest advertised LE 1.82-1.120,
  with a separate 1.30-1.75 legacy fallback per route. Neither selection proves authenticated
  tenant acceptance. See the [M2 file decision](M2_MODERN_FILE_COMPATIBILITY_DECISION.md).
- A structurally valid TOC can omit content because of provider-side visibility rules. Its
  completeness is `unknown`; an empty response is not proof that no content exists.

- The assignments page size control is POST-backed and no GET pagination was found, so a course
  that exceeds one page may be incomplete. The CLI reports completeness evidence and never
  claims a list is exhaustive when it cannot show that.
- Grades and announcements expose no verified total or end marker; completeness is `unknown`.
- Dates shown without a timezone stay timezone-naive in JSON.
- Only one institution's deployment has been used for validation; others are untested.
