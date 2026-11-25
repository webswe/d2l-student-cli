# Syllabus discovery and retrieval

`d2l syllabus find` and `d2l syllabus fetch` let an application locate and download a
course syllabus **through the signed-in student's own existing session**. They are GET-only, add no
privilege, do not touch authentication, and write nothing to disk.

## Status

Implemented from the documented Brightspace Valence content API and tested against a mocked
transport. Institutions differ, so treat automatic retrieval as unproven for any tenant you have not
tried (see "Live verification"). Every parser fails closed (`ProviderEndpointChangedError`), so a
tenant that differs produces a clear error, never partial data.

Verification status:

- Offline, on a mocked transport with synthetic fixtures: table-of-contents parsing and ranking,
  bounded retrieval, media-type and signature checks, and the `.docx` structural check are
  covered by the test suite.
- **Not verified** by any evidence recorded in this repository: retrieval from a live
  institution, for PDF, plain-text or `.docx` files alike. Treat each as unverified until you
  have tried it on your own account.

## Reads performed

| Order | Request | Purpose |
|---|---|---|
| 1 | `GET /d2l/api/versions/` | Read advertised LE versions; this does not prove acceptance at an authenticated route. |
| 2 | `GET /d2l/api/le/{tocVersion}/{orgUnitId}/content/toc` | The course's table of contents. Select the newest advertised 1.82-1.120, or fall back to 1.30-1.75. |
| 3 | `GET /d2l/api/le/{fileVersion}/{orgUnitId}/content/topics/{topicId}/file` | `fetch` only: select the newest advertised 1.82-1.120, or fall back to 1.30-1.75, independently of the TOC selector. If neither range is advertised, fail before a file request. |

No other URL is ever requested. A topic's own `Url` field is read only for its file extension and is
never followed. A topic id is fetched only if the same course's table of contents lists it as a
visible, non-broken file whose extension is `pdf`, `docx`, `txt` or absent.

## Commands

```text
d2l syllabus find  --course <id> [--json]
d2l syllabus fetch --course <id> --topic <topicId> --json
```

`find --json` returns `{"syllabus_search": {...}}` with `course_id`, `api_version`, `topic_count`,
`recommended_topic_id` and `candidates` (`topic_id`, `title`, `module_path`, `kind`,
`file_extension`, `last_modified`, `score`, `reasons`, `fetchable`). `reasons` are fixed codes.
`recommended_topic_id` is non-null only when exactly one downloadable candidate clearly leads;
otherwise the caller chooses.

`fetch --json` returns `{"syllabus_document": {...}}` with `course_id`, `topic_id`, `title`, `module_path`,
`file_name`, `media_type`, `size_bytes`, `sha256`, `api_version` and `content_base64`. `--json` is
required (without it: exit 2, no output). The file name is derived from the table-of-contents title
reduced to `[A-Za-z0-9._ -]`; a name sent by the server is never used.
The `api_version` in `find` describes the TOC request; in `fetch` it describes the file request.
The TOC and file policies currently use the same bounded ranges, but are selected separately
for their respective routes. A `fetchable` candidate means the CLI can attempt the explicit
file GET; it does not prove the tenant will accept the session or grant file permission.

## Safety bounds

- Documents: PDF, Word `.docx` or UTF-8 plain text only, at most 8 MiB, enforced while streaming
  and by `Content-Length`. The bytes are checked against the declared type (`%PDF-` signature, a
  ZIP container validated as below, or no NUL and valid UTF-8); a mismatch is refused. Older
  `.doc`, spreadsheets, presentations and every other format are listed but not fetchable.
- Word documents are accepted only after a structural check of the bytes (never the file name):
  a well-formed, unencrypted ZIP with at most 1,000 parts, no part over 20 MiB, at most 50 MiB
  and a 100:1 compression ratio in total (so a zip bomb is refused before anything is inflated);
  a wordprocessing *document* package (not a template, not macro-enabled); no macro project,
  ActiveX control or embedded object/package; and no DTD or entity declaration in the two parts
  that are inspected. Nothing in the file is executed, rendered or followed; hyperlinks and linked
  resources are never requested. The document is never written to disk.
- Table of contents: at most 10 levels and 10,000 nodes; titles are sanitized and capped.
- Requests: relative Brightspace paths only, TLS verified, identity encoding, redirects never
  followed; an expired session is reported as such. Bodies are never logged.
- If a binary response advertises `Content-Length`, it must be a valid decimal count matching
  the received bytes. An omitted length remains safe under the streamed 8 MiB bound.
- Hidden modules, hidden topics and broken topics are not visible to a student and are dropped.
- TOC lists can omit date-restricted or otherwise unavailable content. A valid empty response is
  not proof that the course has no content, and no TOC result is claimed exhaustively complete.

## Live verification (owner action, value-free)

Only after separate owner approval for a specific tenant, account and route, and after
confirming institutional permission, the owner may consider:

```text
d2l syllabus find --course <id> --json
```

and compare `topic_count`/`candidates` with what the course's Content page shows. A
`CONTENT_*` error names which structural assumption failed (for example `CONTENT_TOC_SHAPE`).
Then `d2l syllabus fetch --course <id> --topic <topicId> --json` for one candidate. Report only
counts and error codes; do not paste course content.

## Ways it may fail on a real tenant (and what each would mean)

None of these has been observed; they are the places where the documented contract and a particular institution can
differ. Each ends in a safe error, never partial data.

| What you see | Likely meaning | Where the fix belongs |
|---|---|---|
| `find` says the session is unauthenticated right after a fresh `d2l auth login` | the tenant does not accept the browser session cookie on the Valence `/d2l/api/le/...` routes (the existing reads use web pages and `/d2l/api/lp/...`) | an authentication decision for the owner; **do not** add scopes or tokens without approval |
| `CONTENT_VERSION_UNSUPPORTED` | No advertised LE version is in the bounded TOC ranges 1.82-1.120 or 1.30-1.75 | the TOC version policy in `brightspace_content.py` |
| `CONTENT_FILE_VERSION_UNSUPPORTED` | TOC listing succeeded, but no advertised LE version is in the bounded file ranges 1.82-1.120 or 1.30-1.75 | review the advertised versions; do not guess or retry another route |
| `CONTENT_VERSIONS_SHAPE` | Version discovery has malformed or conflicting LE data | inspect only safe structural evidence; never guess a version |
| `CONTENT_TOC_SHAPE`, `CONTENT_MODULE_SHAPE`, `CONTENT_TOPIC_*` | the table of contents is shaped differently from the documented schema (or the route returned something else) | the parser and its fixtures |
| `unexpected status 302` on `fetch` | the tenant serves files through a redirect; redirects are refused by design | needs a deliberate, origin-checked decision |
| `403`, "denied this read" | the account may not read course content, or the course hides it | nothing to fix; upload instead |
| a syllabus is only a link or a quiz | listed as a candidate that cannot be fetched | nothing to fix; upload instead |

Report only the error text and counts; never paste course content, URLs, cookies or tokens.
