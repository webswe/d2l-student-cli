# Offline diagnostics contract v1

`d2l diagnostics [--json]` reports local application and configuration facts.
It does not read credential storage, inspect a saved session, make a network
request, or claim provider compatibility. It is safe to use before login.

JSON stdout is one object with exactly these fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1`. |
| `application_version` | Installed package version string, or `unknown`. |
| `python_version` | Local interpreter version string. |
| `configuration_valid` | Boolean result of local base-URL validation. |
| `configuration_error_code` | `invalid_base_url` or null. No URL is echoed. |
| `dependencies` | Boolean availability map for certifi, httpx, keyring, playwright, typer. |
| `local_capabilities` | Static CLI feature names, not provider verification. |
| `session_presence` | Always `not_checked` in v1. |
| `provider_verification` | Always `not_performed` in v1. |
| `known_limitations` | Static evidence limitations. |

The command exits 0 after producing local diagnostics even when configuration
is invalid; the `configuration_valid` field carries that result. It does not
offer `auth status` or session-validity claims. A future verified session
indicator would require separate authorization to access authentication
storage and, for provider validity, a permitted live request.
