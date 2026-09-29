from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("name", "severity", "condition", "duration", "type", "channel", "owner", "runbook")


def _alerts() -> list[dict]:
    return yaml.safe_load((ROOT / "config" / "alert_rules.yaml").read_text(encoding="utf-8"))["alerts"]


def test_three_symptom_based_alerts_are_complete() -> None:
    alerts = _alerts()
    assert len(alerts) == 3
    for alert in alerts:
        for field in REQUIRED:
            assert alert.get(field), f"{alert.get('name')} thiếu {field}"
            assert "TODO" not in str(alert[field])
        assert alert["type"] == "symptom-based"
        assert alert["channel"] == "slack"
        assert re.fullmatch(r"\d+[smh]", alert["duration"])


def test_every_runbook_anchor_exists_and_is_filled() -> None:
    doc = (ROOT / "docs" / "alerts.md").read_text(encoding="utf-8")
    assert "TODO" not in doc
    for alert in _alerts():
        anchor = alert["runbook"].split("#", 1)[1]
        assert f"## {anchor.replace('-', ' ').title()}" in doc


def test_slo_error_budget_matches_target() -> None:
    slo = yaml.safe_load((ROOT / "config" / "slo.yaml").read_text(encoding="utf-8"))["primary_slo"]
    assert round(100 - slo["target_percent"], 6) == slo["error_budget_percent"]
    assert "error_budget_calc" in slo and "rationale" in slo
