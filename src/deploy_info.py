"""Expose a non-secret deployment revision for cross-service verification."""
from __future__ import annotations

import os
import re
import sys


def deploy_revision() -> str:
    raw = os.getenv("RENDER_GIT_COMMIT", "").strip().lower()
    return raw[:12] if re.fullmatch(r"[0-9a-f]{12,64}", raw) else "unknown"


def log_deploy_revision(service: str) -> None:
    # Keep stdout available for cron CLIs whose output is a JSON contract.
    print(
        f"[deploy] service={service} revision={deploy_revision()}",
        file=sys.stderr,
        flush=True,
    )
