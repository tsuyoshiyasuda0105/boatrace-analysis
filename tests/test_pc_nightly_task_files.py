from pathlib import Path


def test_pc_nightly_batch_uses_prepare_script():
    source = Path("scripts/run_pc_nightly_prepare.bat").read_text(encoding="utf-8")

    assert "scripts\\pc_nightly_prepare.py" in source
    assert "01:00" in Path("scripts/install_pc_nightly_prepare_task.ps1").read_text(encoding="utf-8")
    assert "BoatracePcNightlyPrepare" in Path("scripts/install_pc_nightly_prepare_task.ps1").read_text(encoding="utf-8")
