"""
Local Web UI launcher.

Usage:
    python scripts\\run_web.py
    python scripts\\run_web.py --host 0.0.0.0 --port 8080
    python scripts\\run_web.py --version v0.1 --debug
    python scripts\\run_web.py --port 5010 --testing
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.web.app import create_app


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--version", default="v0.8", help="model version")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--testing", action="store_true", help="enable Flask TESTING mode for Playwright/local QA")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = create_app(version=args.version)
    if args.testing:
        app.config["TESTING"] = True
    print(f"Boatrace local UI: http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
