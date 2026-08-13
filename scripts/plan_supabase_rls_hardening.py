"""Print proposed public-schema hardening SQL without applying it."""
from __future__ import annotations

from pathlib import Path


def main() -> int:
    path = (
        Path(__file__).resolve().parents[1]
        / "supabase"
        / "migrations"
        / "202608080002_harden_public_default_privileges.sql"
    )
    print(path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
