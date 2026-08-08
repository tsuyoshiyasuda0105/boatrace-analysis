from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_subscribers_schema_guard_adds_optional_templates_only_when_missing():
    source = (ROOT / "src" / "notifications" / "subscribers.py").read_text(encoding="utf-8")

    assert '("subject_template", "ALTER TABLE alert_subscribers ADD COLUMN subject_template TEXT")' in source
    assert '("body_template", "ALTER TABLE alert_subscribers ADD COLUMN body_template TEXT")' in source
    assert "_ensure_schema(conn)" in source
