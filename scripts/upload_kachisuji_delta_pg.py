# -*- coding: utf-8 -*-
"""Upload a kachisuji delta DB into Postgres transport (kachisuji_delta_files).

Replaces the Supabase-Storage uploader in the nightly pipeline: works with
DATABASE_URL alone, which the PC nightly wrapper already exports from .env.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.kachisuji.delta_transport import prune_transport, upload_delta_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delta", required=True, type=Path)
    parser.add_argument("--prune-days", type=int, default=14)
    args = parser.parse_args()
    if not args.delta.is_file():
        print(f"error: delta not found: {args.delta}", file=sys.stderr)
        return 2
    try:
        info = upload_delta_file(args.delta)
        pruned = prune_transport(days=args.prune_days)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: kachisuji delta upload failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"[uploaded] table=kachisuji_delta_files name={info['name']} "
        f"size={info['size_bytes']} sha={info['sha256'][:12]} pruned={pruned}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
