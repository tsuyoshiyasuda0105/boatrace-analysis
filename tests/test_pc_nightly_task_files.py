from pathlib import Path


def test_pc_nightly_batch_uses_prepare_script():
    source = Path("scripts/run_pc_nightly_prepare.bat").read_text(encoding="utf-8")

    assert "scripts\\pc_nightly_prepare.py" in source
    assert "set \"SCRIPT_DIR=%~dp0\"" in source
    assert "set \"REPO_DIR=%%~fI\"" in source
    assert "C:\\boat_project\\boatrace-analysis\\.venv\\Scripts\\python.exe" in source
    install_source = Path("scripts/install_pc_nightly_prepare_task.ps1").read_text(encoding="utf-8")
    assert "$repo = Split-Path -Parent $PSScriptRoot" in install_source
    assert "01:00" in install_source
    assert "BoatracePcNightlyPrepare" in install_source
