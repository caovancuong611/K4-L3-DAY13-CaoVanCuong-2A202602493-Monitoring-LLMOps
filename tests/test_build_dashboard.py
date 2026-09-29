from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_dashboard", ROOT / "scripts" / "build_dashboard.py")
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)

END = datetime(2026, 9, 29, 8, 0, 30, tzinfo=timezone.utc)


def rec(event: str, seconds: int = 0, **fields) -> dict:
    return {"event": event, "_t": END - timedelta(seconds=seconds), **fields}


def sample() -> list[dict]:
    rows = []
    for i in range(10):
        rows += [
            rec("request_received", 10),
            rec("response_sent", 9, latency_ms=100 + i, ttft_ms=50, cost_usd=0.002,
                tokens_in=30, tokens_out=100, quality_score=0.9, tool_name="retrieval", tool_success=True),
        ]
    rows += [
        rec("request_received", 5),
        rec("request_failed", 5, error_type="RuntimeError", tool_name="retrieval", tool_success=False),
    ]
    return rows


def test_compute_core_numbers() -> None:
    m = bd.compute(sample(), END, 60)
    assert m["traffic"]["count"] == 11
    assert m["errors"]["failed"] == 1
    assert round(m["errors"]["error_rate_pct"], 2) == 9.09
    assert round(m["errors"]["tool_success_rate_pct"], 1) == round(10 / 11 * 100, 1)
    assert m["errors"]["breakdown"] == {"RuntimeError": 1}
    assert m["latency"]["ttft_p95"] == 50
    assert round(m["cost"]["total"], 3) == 0.02
    assert m["tokens"] == {"in": 300, "out": 1000}
    assert round(m["quality"]["mean"], 2) == 0.9


def test_thresholds_follow_operator() -> None:
    assert bd.evaluate(150, "lte", 3000) == "ok"
    assert bd.evaluate(5000, "lte", 3000) == "breach"
    assert bd.evaluate(0.5, "gte", 0.75) == "breach"
    assert bd.evaluate(None, "lte", 1) == "nodata"


def test_records_outside_window_are_ignored() -> None:
    old = [rec("request_received", 3 * 3600)]
    assert bd.compute(old, END, 60)["traffic"]["count"] == 0


def test_html_contains_all_six_panels_and_handles_empty_data() -> None:
    config = yaml.safe_load((ROOT / "config" / "dashboard.yaml").read_text(encoding="utf-8"))
    tz = timezone(timedelta(hours=7))
    for records in (sample(), []):
        page = bd.render(config, bd.compute(records, END, 60), END, tz, {"retrieval_success_rate_pct_min": 90}, None)
        for pid in ("latency", "traffic", "errors", "cost", "tokens", "quality"):
            assert f'id="panel-{pid}"' in page
        assert "TTFT" in page and "Retrieval success" in page
