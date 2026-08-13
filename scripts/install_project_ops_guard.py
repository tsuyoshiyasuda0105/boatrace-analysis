from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "docs" / "project_ops_templates"


def write_text(path: Path, content: str, force: bool) -> str:
    if path.exists() and not force:
        return "skip"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return "write"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install AGENTS.md, handoff, and ops checklist templates into a target project."
    )
    parser.add_argument("--target", required=True, help="Target project directory")
    parser.add_argument("--project-name", default="", help="Optional project name for future expansion")
    parser.add_argument("--date", default="2026-08-09", help="Initial handoff date")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    args = parser.parse_args()

    target = Path(args.target).resolve()
    if not target.exists():
        raise SystemExit(f"target does not exist: {target}")

    mapping = {
        target / "AGENTS.md": (TEMPLATE_DIR / "AGENTS.template.md").read_text(encoding="utf-8"),
        target / "docs" / "handoff.md": (
            (TEMPLATE_DIR / "handoff.template.md").read_text(encoding="utf-8").replace("{{DATE}}", args.date)
        ),
        target / "docs" / "ops_checklist.md": (
            (TEMPLATE_DIR / "ops_checklist.template.md").read_text(encoding="utf-8")
        ),
    }

    results: list[tuple[Path, str]] = []
    for path, content in mapping.items():
        results.append((path, write_text(path, content, force=args.force)))

    for path, status in results:
        print(f"{status.upper():5} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
