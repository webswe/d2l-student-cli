# M2 modern syllabus file compatibility decision

Status: **DOCUMENTED route, OFFLINE VERIFIED implementation, PROVIDER UNVERIFIED**. This
decision authorizes no live Brightspace request or file retrieval. It builds on the
[M1.2 TOC decision](M1_CONTENT_COMPATIBILITY_DECISION.md) and the
[provider validation plan](M1_PROVIDER_VALIDATION_PLAN.md).

## Official contract and limits of that evidence

The [Brightspace Content API reference](https://docs.valence.desire2learn.com/res/content.html)
documents both `GET /d2l/api/le/{version}/{orgUnitId}/content/toc` and
`GET /d2l/api/le/{version}/{orgUnitId}/content/topics/{topicId}/file`. For the file GET,
the published current range starts at LE 1.82; 1.75-1.81 is deprecated and 1.74 or earlier
is obsolete. The route retrieves the underlying file for a file topic and lists
`content:file:read` for LE 1.35 and later. LE 1.34 and earlier instead requires
`content:topics:read`. A 400 can mean the topic is not a file, 403 means permission denied,
404 can mean a missing topic, course, or file, and 429 is a rate limit. The optional `stream`
parameter changes response disposition; this CLI does not set it and never trusts the
advertised filename. [Source](https://docs.valence.desire2learn.com/res/content.html).

The public contract establishes route shape and documented version availability. It does
**not** establish that a particular tenant advertises those versions, accepts this CLI's
browser session at either authenticated route, grants the signed-in student file access,
returns an allowed document type, or leaves server-side state unchanged after a GET.

## Decision and version policy

Enable modern file-route selection for an explicit `d2l syllabus fetch`. The route is
documented at modern LE versions, and the existing service already requires a visible file
topic from the requested course's TOC before any file GET. It also bounds and validates the
returned document. An additional compatibility flag would not verify provider behavior;
separate owner approval remains mandatory for any live test.

| Capability | Selection from advertised LE `SupportedVersions` | On no usable version |
|---|---|---|
| TOC listing | Highest 1.82-1.120, else highest 1.30-1.75 | `CONTENT_VERSION_UNSUPPORTED` |
| Explicit file retrieval | Highest 1.82-1.120, else highest 1.30-1.75 | `CONTENT_FILE_VERSION_UNSUPPORTED` |

The two policies have separate constants and route-specific selection calls. Neither uses
1.76-1.81 as a fallback or selects above 1.120. Discovery data with duplicate LE product
entries, malformed versions or unsupported types fails closed with a fixed error. No
endpoint-failure fallback, version guessing or retry at a different version occurs. A
successful `/d2l/api/versions/` response proves advertised versions only; each approved
authenticated route needs its own session and permission evidence.

The prior modern-only `find` behavior that marked candidates non-fetchable is superseded.
With a supported modern version, `find` may mark a suitable file topic fetchable and
recommend it; this is a local capability assessment, **not** proof of provider permission.
The `api_version` field still reports the TOC version in `find` and the file version in
`fetch`. JSON field names and CLI command flags are unchanged.

## Security and failure behavior

Only the explicit syllabus `fetch` command reaches the file route. The service requires the
topic to appear as a visible, non-broken file in the selected course's TOC, validates numeric
course and topic IDs, and rejects unsupported extensions. Topic `Url` and file
`Content-Disposition` values never become request targets or output filenames. The HTTP
boundary keeps GET-only HTTPS, TLS verification, same-origin requests, no redirect following,
identity encoding and an 8 MiB streamed file limit. A present binary `Content-Length` must
be a valid decimal count matching the bytes received; a missing length is allowed because
the streaming bound still applies. PDF signature, DOCX ZIP/XML structure, and UTF-8 text
checks remain in place. No document is written to disk or executed.

401, 403, 404, 429, unsolicited partial responses, redirects, malformed media types and
malformed documents raise fixed,
sanitized errors. No response body, cookie, URL with identifiers, or provider filename enters
the error message. The CLI never retries with another API version or follows a file URL.

## Evidence and remaining go/no-go gate

Fictional mocked tests cover modern-only and mixed-version selection, legacy fallback,
malformed discovery, the three allowed document types, byte and media-type rejection,
invalid and truncated lengths, redirects, permission failures, unsafe DOCX content,
course/topic restrictions, hostile URLs and filenames, and CLI JSON output. Full suite,
lint, type, build and CI results belong in the M2 PR and are not provider evidence.

**GO for owner review of the offline implementation. PROVIDER UNVERIFIED for live use.**
Before a live compatibility decision, the owner must separately approve a specific tenant,
synthetic account/course/file, exact TOC and file route versions, institutional permission,
evidence fields, and stop conditions. Confirm session acceptance and student permission
independently at each authenticated route, then compare only safe structural and outcome
observations. Do not retain identifiers, raw URLs, response bodies, document bytes, grades,
credentials, headers or screenshots in the public repository. An unexpected response,
redirect, forbidden access, state change, or uncertain side effect is a no-go. No real
provider validation occurred in M2.
