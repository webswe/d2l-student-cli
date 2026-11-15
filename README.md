# d2l-cli

A personal, GET-only command-line tool for **your own** Brightspace/D2L
student account, currently built and tested against Kennesaw State
University's Brightspace. Unofficial: not affiliated with or endorsed by D2L
Corporation or any university. Not a general-purpose Brightspace client —
scoped narrowly to what one student needs to read their own data.

> **Public beta (source preview).** This repository is a beta of the standalone command-line tool.
> Stable v0.1.0 has not been released or tagged, and there is no PyPI package or installer: install
> from source as described below. Windows is the only validated operating system, and Kennesaw State
> University is the only institution validated against a live deployment; other institutions are not
> guaranteed to work. Syllabus retrieval (PDF, Word `.docx`, plain text) is covered by offline tests only
> and has not been independently confirmed against a live institution. Every Brightspace operation is
> read-only, and you complete SSO and MFA yourself. Report bugs through GitHub Issues, and never post
> credentials, cookies, session data or personal academic data in an issue.

## Who it is for and what is supported

For a student who wants to read their **own** courses, assignments, visible grades,
announcements and syllabus files from a terminal, or to feed them (as JSON) to their own local
tools. It cannot change anything in Brightspace.

| | Status |
|---|---|
| OS | **Windows 10/11 only** (needs Windows Credential Manager) |
| Python | 3.11 validated; 3.12 and 3.13 pass the offline test suite |
| Institution | **Kennesaw State University** is the only tenant validated. Others are untested. |
| Install | From source with `uv`. **Not published on PyPI.** |

Supported environments and limits are listed in the table above and in the sections below.

**Names.** The GitHub repository is `d2l-student-cli`. The Python distribution (as shown by
`pip`/`uv`) is `d2l-cli`, the import package is `d2l_cli`, and the command is `d2l`.

## Prerequisites

- Windows 10 or 11 with Windows Credential Manager available.
- [Git](https://git-scm.com/downloads), to clone the repository.
- Python 3.11 or newer.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
- Network access to GitHub, PyPI and Playwright's browser download during setup, and to your
  institution's Brightspace site afterwards.
- A working institutional Brightspace account. You must complete SSO and MFA yourself; the CLI
  never asks for or automates those credentials.

Windows with Python 3.11, 3.12 and 3.13 is covered by the automated test matrix. Only one
institution's Brightspace has been validated (see the table above); any other institution is
unverified.

## Install on Windows

Setup is four separate steps. None of them signs you in.

**1. Get the source.** In Windows PowerShell:

```powershell
git clone https://github.com/webswe/d2l-student-cli.git
cd d2l-student-cli
```

**2. Install the locked Python dependencies** into the project's own `.venv` (nothing is installed
globally):

```powershell
uv sync --locked
```

**3. Install the browser runtime.** Installing the Python package does not install Chromium, which
`auth login` uses:

```powershell
uv run playwright install chromium
```

**4. Check the installation:**

```powershell
uv run d2l --version
uv run d2l --help
```

Steps 1 to 3 need network access and can take a few minutes on a first run.

## First login and first read

Logging in needs you, in front of the computer. The CLI cannot complete it on its own.

```powershell
uv run d2l auth login
```

1. An isolated Chromium window opens on your institution's Brightspace site.
2. You complete your institution's normal SSO and MFA in that window. How long this takes depends
   on your institution; the CLI waits for you (up to 10 minutes).
3. When Brightspace loads, the CLI stores the session (see below) and closes the browser context.

Then check the session and read some data:

```powershell
uv run d2l auth status
uv run d2l courses list
uv run d2l courses list --json
```

`auth status` makes one read-only course-list request to confirm that the saved session is still
accepted. A locally present session is not reported as authenticated unless that request succeeds.

### What is stored, and what it is bound to

- **Stored:** the cookies Chromium holds for the configured Brightspace origin, as one `Cookie`
  header value plus the cookie names, together with a format version and the origin they belong
  to, in a single Windows Credential Manager entry (service `d2l-student-cli`, key `session`).
  Cookies for other sites are not captured. No password or MFA code is stored, and nothing is
  written to a file.
- **Isolated:** that service name belongs to this project. This tool does not read, overwrite or
  delete entries that belong to other applications, and there is no automatic migration of
  credentials from anywhere else.
- **Bound to one origin:** the entry records the canonical origin the session was created for
  (`https://host`, plus the port when it is not 443). Before any request is built, the CLI checks
  that the configured site is exactly that origin: same scheme, host and effective port. A
  different case, a trailing `/` or an explicit `:443` is the same origin; a different host, a
  subdomain, a different port or `http` is not. On a mismatch the command stops without
  contacting any site and tells you to log in again; the stored cookies are never sent to another
  origin. Changing `D2L_CLI_BASE_URL` therefore requires a fresh `d2l auth login`, which replaces
  the stored session.
- **Older records:** a record written by a pre-release build has no recorded origin and cannot be
  reused. Run `d2l auth login` once; nothing is deleted, migrated or repaired automatically.
- **Expiry:** an expired session is detected from the provider's login redirect, and the command
  stops with a "log in again" error and a non-zero exit code. Nothing is renewed or deleted
  automatically.
- **Logout:** there is no `logout` command. To remove the session, delete the Windows Credential
  Manager entry described under "Update and uninstall" below.

## What it does today

| Command | What it does |
|---|---|
| `uv run d2l auth login` | Opens an isolated browser; you complete your institution's normal SSO/MFA by hand. The resulting session is stored securely. |
| `uv run d2l auth status` | Reports whether the stored session is accepted by Brightspace right now (it makes one real read-only request; local presence alone is not "authenticated"). |
| `uv run d2l courses list` | Lists your enrolled courses (tab-separated: code, name, semester). |
| `uv run d2l courses list --json` | Same, as JSON on stdout: `{"courses": [...]}`. |
| `uv run d2l assignments list --course <id>` | Lists a course's assignments (tab-separated: name, status, score, due). |
| `uv run d2l assignments list --course <id> --json` | Same, as JSON on stdout: `{"assignments": [...]}`. |
| `uv run d2l assignments list --course <id> --json --include-completeness` | Adds evidence-bounded page-size metadata without changing assignment objects. |
| `uv run d2l assignments overview` | Lists assignments across active accessible courses (course code, course name, assignment, due). |
| `uv run d2l assignments overview --chronological` | Sorts recognized due dates earliest first, with missing dates last. |
| `uv run d2l assignments overview --json --include-completeness` | Adds per-course page-size metadata to the overview JSON. |
| `uv run d2l grades list --course <id>` | Lists one course's visible grade items (tab-separated: item, points, grade, weight, feedback availability). |
| `uv run d2l grades list --course <id> --json` | Same, as JSON on stdout: `{"grade_items": [...]}`. |
| `uv run d2l grades list --course <id> --json --include-completeness` | Adds the evidence-bounded top-level `"completeness": "unknown"` field. |
| `uv run d2l announcements list --course <id>` | Lists one course's announcement titles with short sanitized body previews. |
| `uv run d2l announcements list --course <id> --details` | Shows each complete sanitized plain-text announcement body. |
| `uv run d2l announcements list --course <id> --json` | Emits the stable `{"announcements": [...]}` JSON object, including each complete sanitized body already present in the listing response. |
| `uv run d2l syllabus find --course <id> [--json]` | Ranks the course's visible content topics that may be its syllabus (read-only; see docs/SYLLABUS_DISCOVERY.md). |
| `uv run d2l syllabus fetch --course <id> --topic <topicId> --json` | Downloads one listed PDF, Word `.docx` or plain-text file (at most 8 MiB) as base64 JSON. |
| `uv run d2l calendar list --course <id> [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--json]` | Lists your calendar events for one course in a bounded UTC window (default: next 30 days). Recurring events are reported once, never expanded. See docs/ACTIVITIES.md. |
| `uv run d2l calendar upcoming [--days N] [--all-courses] [--json]` | Lists upcoming events across your active accessible courses; courses that cannot be read are named and the result is marked incomplete. |
| `uv run d2l announcements list --course <id> --json --include-completeness` | Adds the evidence-bounded top-level `"completeness": "unknown"` field. |
| `uv run d2l overview` | Groups retrieved assignment, visible-grade, and announcement counts plus compact assignment and announcement previews by active accessible course. |
| `uv run d2l overview --details` | Shows all retrieved assignments, exact displayed grade-item details, and complete sanitized announcement bodies. |
| `uv run d2l overview --json` | Emits the full grouped academic overview as JSON. |
| `uv run d2l overview --all-courses` | Includes inactive accessible courses; inaccessible courses remain excluded. |

`<id>` is the Brightspace course (org-unit) id shown in the `courses list --json` output (`id`). Placeholders only
in the examples here; no course lookup by name is performed.

The syllabus commands are covered by offline tests on a mocked transport only. Retrieval of a
PDF, `.docx` or text file from a live institution has not been independently confirmed; see
[`docs/SYLLABUS_DISCOVERY.md`](docs/SYLLABUS_DISCOVERY.md).

Errors go to stderr with a non-zero exit code, so `--json` stdout stays pure
JSON.

### JSON from PowerShell

JSON modes are designed for scripting. For example, this reads the course
envelope without mixing warnings into the JSON stream:

```powershell
$courseResult = uv run d2l courses list --json | ConvertFrom-Json
$courseResult.courses.Count
```

Use the numeric `id` from that result for a course-scoped command. This
fictional example writes only sanitized Announcement JSON:

```powershell
uv run d2l announcements list --course 123456 --json |
    Set-Content -Encoding utf8 announcements.json
```

Treat exported files as private academic records. JSON completeness metadata
is opt-in where the standalone command supports
`--include-completeness`; Academic Overview JSON always carries its existing
per-source completeness fields.

The complete external-consumer contract, including field types, nullability,
identity scope, ordering, exit behavior, and safe subprocess examples, is in
[`docs/MACHINE_INTERFACE.md`](docs/MACHINE_INTERFACE.md).

### Assignment output

Each assignment has `id`, `course_id`, `name`, `submission_status`, `score`
and an optional `due_date`.

- `submission_status` and `score` are the text Brightspace displays; they are
  not interpreted.
- `due_date` is the due date/time Brightspace displays for that assignment, or
  `null` (JSON) / `-` (text) when the list page shows none. It is
  timezone-naive on purpose: Brightspace shows no timezone, so none is
  assumed — it is not UTC and not your machine's timezone. In JSON it is an
  ISO-8601 string without an offset (for example `2026-01-31T23:59:00`).
- In text output the due date is the last column, so the earlier columns keep
  their positions.
- Availability dates, feedback, and assignment detail are **not** supported.
- Only assignments the list page identifies by a link are returned; a
  date-bearing row without one (seen once during reconnaissance) is ignored
  rather than guessed at.
- Each course read consumes one server-rendered list response. Live responses
  currently fit below Brightspace's selected page-size limit, but the page-size
  control is POST-backed and no GET pagination path was observed. A course that
  exceeds that limit may therefore be incomplete under this CLI's GET-only
  policy; see `docs/PROVIDER_NOTES.md`.

The CLI assesses that boundary before assignment normalization, so linkless,
duplicate, course-mismatched, and unfamiliar multi-cell data rows cannot make
the observed count artificially smaller. It warns on stderr when the count
reaches the selected page size (`potentially_truncated`) or when the selector is
missing or ambiguous (`unknown`). A count below the selected size is reported
only as `below_page_size_boundary`, never as proof that the response is
complete. Default JSON remains unchanged; `--include-completeness` explicitly
adds `completeness` or `course_completeness` metadata.

### Cross-course assignment overview

`d2l assignments overview` composes the existing course-list and assignment-list
GET requests; it does not use another Brightspace endpoint. By default it reads
active, accessible courses. Pass `--all-courses` to include inactive accessible
courses, or `--json` for a stable `{"assignments": [...]}` response containing
`id`, `course_id`, `course_code`, `course_name`, `name`, and `due_date`.

`--chronological` sorts recognized timezone-naive due dates earliest first and
places assignments without a recognized date last. It does not compare dates to
the current clock and does not label assignments overdue, due soon, complete, or
incomplete.

### Academic overview

`d2l overview` composes the existing course, Assignment, Grades, and
Announcements services; it adds no endpoint. The default text view preserves
course/provider order and, for each active accessible course, labels counts as
**retrieved assignments**, **visible grade items**, and **retrieved
announcements**. It also reports how many retrieved assignments have a
recognized due date and shows at most three assignment entries plus at most
three announcement titles. `--details` shows every retrieved assignment, the
exact displayed points, grade, weight, and feedback-availability fields for
visible grade items, and each complete sanitized announcement body. Due dates
remain timezone-naive provider display values. Announcement order is provider
order and is not described as recent or latest.

JSON always includes the full grouped data. Its top-level `courses` array
contains each normalized course; the existing assignment and grade counts,
completeness metadata, and ordered records; plus
`retrieved_announcement_count`, `announcement_completeness`, and ordered
`announcements`. Each announcement uses the standalone command's established
`course_id`, `announcement_id`, `title`, `body`, and `detail_reference` shape.
Announcement completeness is always `unknown`. Default filtering matches
`assignments overview`; `--all-courses` adds inactive accessible courses but
never inaccessible courses.

One invocation performs one course-list GET and, for each selected course, one
existing Assignment GET, the two established Grades GETs, and one existing
News-list GET: `1 + 4N` requests for `N` selected courses. No announcement
detail page is fetched. Any provider failure aborts the overview instead of
returning a silently partial result. Completeness warnings are aggregated by
source and contain only counts, not course identifiers or academic values.

The overview does not infer submission/completion status, overdue or due-soon
state, missing work, grade performance, overall grades, weighted averages, or
GPA, or announcement recency, authorship, urgency, or read state. Assignment,
grade, and announcement completeness statuses retain their existing narrow
meanings.

### Grade output

`d2l grades list` requires one explicit numeric course id and preserves the
visible item ordering from Brightspace. Each item contains `course_id`, `label`,
`displayed_points`, `displayed_grade`, `displayed_weight`, and
`feedback_availability`. The three displayed-value fields are unmodified strings
or `null`; they are not parsed into scores or percentages. Text output uses `-`
for an absent displayed value, which remains distinct from a displayed `0`.

Feedback availability is `none`, `present`, or `unknown`; feedback content is
never read into the model or displayed. Category/group rows are excluded.
Recognized summary rows and unsupported table geometry fail the entire request
rather than returning a potentially misaligned or partial list.
The command does not calculate averages, weighted grades, overall grades, GPA,
or academic states such as missing, exempt, hidden, or unreleased.

Brightspace exposes no verified grade-list total, page-size boundary, or
end-of-list marker. Text mode therefore emits one stderr warning that
completeness cannot be verified. Default JSON remains unchanged; opt in with
`--include-completeness` to add `"completeness": "unknown"`. This status does
not mean truncation was observed—it means neither completeness nor truncation
can be established.

### Announcement output

`d2l announcements list` requires one explicit numeric course id and preserves
the order of the course's News listing. The default text view shows a numbered,
single-line title and a body preview of at most 120 characters. `--details`
shows every complete sanitized body beneath its title, retaining paragraph and
list line boundaries. An absent or sanitization-empty body is displayed as
`(empty body)`. Titles and bodies are plain text only; tabs, newlines, ANSI/OSC
sequences, terminal controls, markup, hidden content, and active/resource
elements cannot control terminal presentation. The command does not follow
detail links or embedded resources.

JSON uses the stable top-level shape `{"announcements": [...]}`. Every record
contains exactly `course_id`, `announcement_id`, `title`, `body`, and
`detail_reference`; `body` is sanitized plain text or `null`, and
`detail_reference` is a validated relative reference that is reported as data,
not fetched. Dates, authors, urgency, pinned state, and read/unread state are
not supported and do not appear.

The 120-character limit applies only to the default human preview. JSON always
contains the same complete normalized body that `--details` prints; adding
`--details` to a JSON invocation does not change its schema or values. This is
independent of list completeness: a full body can be available for every
returned record while the set of returned announcements still has
`"completeness": "unknown"`.

Brightspace exposes no verified announcement total, page-size boundary, or
end-of-list marker. Text mode therefore emits one stderr warning that
completeness cannot be verified. JSON emits no warning and has no completeness
field by default; `--include-completeness` explicitly adds
`"completeness": "unknown"`. Unknown does not claim that truncation occurred.
One command invocation makes exactly one authenticated News-listing GET.

## Update and uninstall

- **Update the source:** `git pull`, then `uv sync --locked`. Dependency versions change only
  when `uv.lock` changes.
- **Remove local files:** delete the cloned folder (its `.venv` is inside it). Nothing is
  installed globally.
- **Remove stored login:** your session lives in Windows Credential Manager as a generic
  credential named `d2l-student-cli` (user name `session`). Deleting the folder does **not**
  remove it. Open Control Panel, Credential Manager, Windows Credentials, find the entry whose
  name is `d2l-student-cli` (or ends in `@d2l-student-cli`), and remove it there. There is no
  `logout` command, and nothing is deleted automatically.

## Security model

These are implemented controls, not guarantees; see
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) for the reasoning.

- **No password or MFA code is ever handled.** Login happens in your
  institution's real browser SSO flow, which you complete manually.
- **Session storage.** The session cookies are stored only through Windows
  Credential Manager (via `keyring`); the tool refuses any other storage
  backend rather than fall back to a file. No plaintext credential files, no
  `.env` secrets.
- **GET-only.** The application's request surface exposes only GET-performing
  methods plus `close`; nothing in this project issues POST/PUT/PATCH/DELETE.
  One narrowly validated method can follow the established Grades redirect by
  issuing one additional GET. This is a client-side boundary — the underlying
  Brightspace credential itself is **not** read-only.
- **Origin-bound requests.** Every request target must be a relative
  Brightspace path. Absolute URLs (including same-origin ones) and
  scheme-relative targets are rejected before any request is built, so the
  session cookie is not sent to another origin through the client. The
  configured base URL must be a plain `https` origin.
- **TLS verification is always on** and not configurable.
- **Redirects are not followed automatically.** A redirect to the Brightspace
  login page, or the small login page Brightspace sometimes returns instead,
  is reported as an expired session (`d2l auth login` again). The Grades
  provider follows exactly one established relative, same-origin redirect only
  after validating its path and course association. The tool never logs in on
  its own.
- **Logs and errors** are built to exclude cookie/header values and response
  bodies.
- **Bounded response parsing.** Authenticated response bodies are streamed
  through a fixed application limit before JSON or HTML parsing.
- **Terminal-safe text.** Every provider-controlled string in human-readable
  output has terminal controls and unsafe Unicode formatting controls removed.
  The established JSON contract remains unchanged.

Brightspace pages are untrusted content: names and text from the LMS are
treated as data only. Plain-text and terminal sanitization do not defeat
natural-language prompt injection; downstream AI consumers must not treat
academic content as instructions.

## Not implemented

Calendar, quizzes, discussions, notifications / upcoming work, assignment
detail, submission of any kind, grade feedback content,
availability dates, and any integration with other tools. Reconnaissance
history (what was observed, and why the current scope is what it is) is in
[`docs/PROVIDER_NOTES.md`](docs/PROVIDER_NOTES.md).

## Configuration

The Brightspace base URL defaults to KSU's. To point at another Brightspace
site, set `D2L_CLI_BASE_URL` to a plain `https://host` origin (no path, query,
credentials or fragment); anything else is rejected. Other institutions are
untested.

```powershell
$env:D2L_CLI_BASE_URL = "https://learn.example.edu"
uv run d2l auth login
```

Remove a temporary override with
`Remove-Item Env:D2L_CLI_BASE_URL`. A stored session is bound to the origin it was created
for: after changing the base URL, run `d2l auth login` again. The CLI refuses to use a session
that belongs to another site (see "What is stored, and what it is bound to" above).

## Troubleshooting

| Symptom | Action |
|---|---|
| `d2l` does not launch or a module is missing | From the repository root, run `uv sync --locked`, then `uv run d2l --help`. |
| Playwright reports that the Chromium executable is missing | Run `uv run playwright install chromium`. Browser installation is a separate setup step. |
| No stored session, expired session, or an unexpected login redirect | Run `uv run d2l auth login`, complete SSO/MFA manually, then check `uv run d2l auth status`. |
| Secure credential storage is unavailable | Run on Windows with Windows Credential Manager available. The CLI deliberately refuses plaintext or third-party fallback keyrings. |
| The base URL is rejected | Use a plain HTTPS origin only, such as `https://learn.example.edu`; remove paths, query strings, fragments, and embedded credentials. |
| A course identifier is rejected | Use the numeric `id` from `uv run d2l courses list --json`; names and codes are not accepted as identifiers. |
| Permission, provider-structure, content-type, or transport error | Confirm the network and session with `auth status`. Retry only after the underlying condition changes; these errors fail closed and are not empty-list results. |
| An empty list is returned successfully | The provider returned no supported records for that scope. This is distinct from a retrieval failure. Review the accompanying completeness warning or JSON metadata before assuming the list is exhaustive. |

Diagnostics intentionally omit authenticated URLs, identifiers, response
bodies, credentials, and academic content. If a fixed provider error persists
after a fresh login, retain the error code—not the page contents—when
reporting it.

## Completeness and interpretation limits

Retrieved records are not automatically guaranteed to be every record held by
Brightspace. Assignment completeness is bounded only by the observed page-size
evidence; Grades and Announcements have no verified total or end marker and
therefore report `unknown`. `unknown` does not assert that truncation occurred.

The CLI does not infer local timezones, overdue or missing-work states,
academic performance, grade calculations, Announcement recency, or read state.
Text warnings go to stderr; supported JSON metadata is documented with each
command above.

## Version and release status

This project is pre-release. Release history is recorded in
[`CHANGELOG.md`](CHANGELOG.md) and the release policy in
[`docs/RELEASING.md`](docs/RELEASING.md). It is validated against one institution's Brightspace
(see the table at the top), not a claim of general Brightspace compatibility. Installation is from
source with `uv`; no package-index publication is required or implied.

## Documentation

[Architecture](docs/ARCHITECTURE.md) · [Threat model](docs/THREAT_MODEL.md) ·
[Security policy](SECURITY.md) · [Machine interface](docs/MACHINE_INTERFACE.md) ·
[Development](docs/DEVELOPMENT.md) · [Contributing](CONTRIBUTING.md) ·
[Releasing](docs/RELEASING.md) · [Changelog](CHANGELOG.md) · [Support](SUPPORT.md) ·
[Troubleshooting](docs/TROUBLESHOOTING.md) · [Provider notes](docs/PROVIDER_NOTES.md) ·
[Syllabus discovery](docs/SYLLABUS_DISCOVERY.md)

## License

D2L-CLI is released under the [MIT License](LICENSE). Third-party dependencies keep their own
licenses. Playwright downloads Chromium separately, under its own terms; neither Chromium nor any
dependency is relicensed by this project. Content you retrieve from Brightspace belongs to your
institution and its authors, not to this project.

## Disclaimer

D2L-CLI is an independent project. It is not affiliated with, endorsed by, or supported by D2L
Corporation, Brightspace, or any university. Use it only with your own account and in accordance
with your institution's policies.

## Development

```sh
uv sync --locked
uv run d2l --help
uv run pytest -q
uv run ruff check .
uv run mypy
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

The test suite uses only local fakes and mocked HTTP: it never contacts
Brightspace or touches the real credential store.
