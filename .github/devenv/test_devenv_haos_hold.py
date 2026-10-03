"""Hold an HAOS embedded-server instance up for live testing.

Copied into tests/src/e2e/ by dev-haos-env.yml and run with HAOS_TEST_MODE=
embedded, so the e2e session fixture does the real bring-up: the HAOS image,
the checkout's wheel staged for the embedded server, the entry enabled. This
writes HA's URL and the embedded server's webhook URL to DEVENV_INFO, then
sleeps until DEVENV_MINUTES elapse.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def test_hold_haos_embedded(ha_container_with_fresh_config):
    info = ha_container_with_fresh_config
    Path(os.environ["DEVENV_INFO"]).write_text(
        json.dumps(
            {
                "base_url": info["base_url"],
                "embedded_webhook_url": info["embedded_webhook_url"],
                "token": info["token"],
                "backend": info.get("backend"),
            }
        )
    )
    end = time.monotonic() + 60 * int(os.environ.get("DEVENV_MINUTES", "300"))
    while time.monotonic() < end:
        time.sleep(30)
