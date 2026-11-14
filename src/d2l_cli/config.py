"""Non-secret configuration boundary.

The Brightspace base URL is not a secret (see docs/THREAT_MODEL.md), so it
deliberately does not go through `d2l_cli.credentials`/OS-protected
storage -- that machinery exists for session material, not for a plain
institution URL. Kept in its own tiny module, separate from secret
handling, so a future multi-institution config source (a file, a CLI flag)
can replace ``load_config()``'s body without callers changing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from d2l_cli.client.http import canonical_origin

DEFAULT_BASE_URL = "https://kennesaw.view.usg.edu/"


@dataclass(frozen=True)
class Config:
    base_url: str = DEFAULT_BASE_URL


def load_config() -> Config:
    """Load configuration: an optional ``D2L_CLI_BASE_URL`` override of the default site.

    The value is validated here, before any credential is read, so an unsafe or malformed site
    is reported cleanly instead of reaching the credential store or the network.
    """
    base_url = os.environ.get("D2L_CLI_BASE_URL", DEFAULT_BASE_URL)
    canonical_origin(base_url)
    return Config(base_url=base_url)
