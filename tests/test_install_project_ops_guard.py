from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_install_project_ops_guard(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    target = tmp_path / "sample-project"
    target.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "install_project_ops_guard.py"),
            "--target",
            str(target),
            "--date",
            "2026-08-09",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert (target / "AGENTS.md").exists()
    assert (target / "docs" / "handoff.md").exists()
    assert (target / "docs" / "ops_checklist.md").exists()
    assert "WRITE" in result.stdout
    assert "2026-08-09" in (target / "docs" / "handoff.md").read_text(encoding="utf-8")


def test_project_ops_guard_skill_template_validates():
    repo_root = Path(__file__).resolve().parents[1]
    skill_dir = repo_root / "docs" / "project_ops_templates" / "project-ops-guard"
    validator = Path(
        "C:/Users/tsuyo/.codex/skills/.system/skill-creator/scripts/quick_validate.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(validator),
            str(skill_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "VALID" in result.stdout.upper()
