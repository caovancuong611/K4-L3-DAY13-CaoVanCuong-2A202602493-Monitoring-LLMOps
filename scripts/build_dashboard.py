"""Dựng dashboard 6 panel từ data/logs.jsonl theo config/dashboard.yaml.

Chạy:
    python scripts/build_dashboard.py                # ghi submission/evidence/dashboard.html
    python scripts/build_dashboard.py --watch        # tự dựng lại mỗi refresh_seconds

Mở file HTML bằng trình duyệt rồi chụp ảnh làm evidence 11-dashboard-overview.
Cửa sổ thời gian là 60 phút kết thúc tại bản ghi log mới nhất (dùng --end now để kết thúc tại giờ hiện tại).
"""
from __future__ import annotations

import argparse
import html
import json
import math
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio
from app.metrics import percentile

DEFAULT_LOG = REPO_ROOT / "data" / "logs.jsonl"
DEFAULT_CONFIG = REPO_ROOT / "config" / "dashboard.yaml"
DEFAULT_SLO = REPO_ROOT / "config" / "slo.yaml"
DEFAULT_OUT = REPO_ROOT / "submission" / "evidence" / "dashboard.html"

SERIES_COLORS = {"p50": "#2563eb", "p95": "#d97706", "p99": "#9333ea", "ttft": "#0f766e"}


# --------------------------------------------------------------------------- dữ liệu
def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            rec["_t"] = parse_ts(rec["ts"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        records.append(rec)
    return records


def floor_minute(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


def _pct(values: list[float], p: int) -> float | None:
    return percentile(values, p) if values else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def compute(records: list[dict], end: datetime, minutes: int = 60) -> dict:
    """Tổng hợp mọi số liệu cho 6 panel trong cửa sổ [end-minutes, end]."""
    end_b = floor_minute(end)
    buckets = [end_b - timedelta(minutes=minutes - 1 - i) for i in range(minutes)]
    index = {b: i for i, b in enumerate(buckets)}
    per = [
        {"recv": 0, "fail": 0, "lat": [], "ttft": [], "cost": 0.0, "tin": 0, "tout": 0, "q": []}
        for _ in buckets
    ]
    errors: Counter[str] = Counter()
    tool_ok = tool_total = 0
    received_times: list[datetime] = []
    for rec in records:
        i = index.get(floor_minute(rec["_t"]))
        if i is None:
            continue
        event, slot = rec.get("event"), per[i]
        if event == "request_received":
            slot["recv"] += 1
            received_times.append(rec["_t"])
        elif event == "request_failed":
            slot["fail"] += 1
            errors[str(rec.get("error_type") or "unknown")] += 1
        elif event == "response_sent":
            slot["lat"].append(rec.get("latency_ms", 0))
            slot["ttft"].append(rec.get("ttft_ms", 0))
            slot["cost"] += rec.get("cost_usd", 0.0)
            slot["tin"] += rec.get("tokens_in", 0)
            slot["tout"] += rec.get("tokens_out", 0)
            if rec.get("quality_score") is not None:
                slot["q"].append(rec["quality_score"])
        if rec.get("tool_success") is not None and event in {"response_sent", "request_failed"}:
            tool_total += 1
            tool_ok += bool(rec["tool_success"])

    lat = [v for s in per for v in s["lat"]]
    ttft = [v for s in per for v in s["ttft"]]
    quality = [v for s in per for v in s["q"]]
    recv = sum(s["recv"] for s in per)
    fail = sum(s["fail"] for s in per)
    span_min = 1.0
    if received_times:
        span_min = max(1.0, math.ceil((max(received_times) - min(received_times)).total_seconds() / 60))
    cost_total = sum(s["cost"] for s in per)
    responses = len(lat)
    return {
        "buckets": buckets,
        "per": per,
        "latency": {
            "p50": _pct(lat, 50), "p95": _pct(lat, 95), "p99": _pct(lat, 99), "ttft_p95": _pct(ttft, 95),
        },
        "traffic": {"count": recv, "rate_per_minute": (recv / span_min) if recv else None},
        "errors": {
            "received": recv,
            "failed": fail,
            "error_rate_pct": (fail / recv * 100) if recv else None,
            "breakdown": dict(errors),
            "tool_success_rate_pct": (tool_ok / tool_total * 100) if tool_total else None,
            "tool_total": tool_total,
        },
        "cost": {"total": cost_total, "avg": (cost_total / responses) if responses else None},
        "tokens": {"in": sum(s["tin"] for s in per), "out": sum(s["tout"] for s in per)},
        "quality": {"mean": _mean(quality)},
    }


def threshold_value(panel_id: str, aggregation: str, m: dict) -> float | None:
    """Giá trị thực tế tương ứng với threshold.aggregation của panel."""
    table = {
        ("latency", "p95"): m["latency"]["p95"],
        ("traffic", "rate_per_minute"): m["traffic"]["rate_per_minute"],
        ("errors", "error_rate_pct"): m["errors"]["error_rate_pct"],
        ("cost", "total"): m["cost"]["total"] if m["cost"]["avg"] is not None else None,
        ("tokens", "sum_by_field"): max(m["tokens"]["in"], m["tokens"]["out"]) or None,
        ("quality", "mean"): m["quality"]["mean"],
    }
    return table.get((panel_id, aggregation))


def evaluate(value: float | None, operator: str, limit: float) -> str:
    if value is None:
        return "nodata"
    ok = value <= limit if operator == "lte" else value >= limit
    return "ok" if ok else "breach"


# --------------------------------------------------------------------------- SVG
def _nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * exp:
            return step * exp
    return 10 * exp


def _fmt(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "–"
    return f"{value:,.{digits}f}"


def chart_svg(labels: list[str], series: list[dict], kind: str, hline: dict | None = None,
              unit: str = "", digits: int = 0, w: int = 560, h: int = 200) -> str:
    """series: [{name, values(list[float|None]), color, dash}]. kind: line | bar."""
    left, right, top, bottom = 52, 14, 12, 26
    pw, ph = w - left - right, h - top - bottom
    n = len(labels)
    peak = max([v for s in series for v in s["values"] if v is not None] + ([hline["value"]] if hline else []) + [0])
    if peak <= 0:
        return f'<div class="empty">Chưa có dữ liệu trong 60 phút gần nhất</div>'
    ymax = _nice_max(peak * 1.08)

    def x_at(i: int) -> float:
        return left + (i + 0.5) * pw / n

    def y_at(v: float) -> float:
        return top + ph - (v / ymax) * ph

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" class="chart">']
    for frac in (0, 0.5, 1):
        y = top + ph - frac * ph
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{w - right}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" class="axis" text-anchor="end">{_fmt(frac * ymax, digits)}</text>')
    for i in sorted({0, n // 4, n // 2, 3 * n // 4, n - 1}):
        parts.append(f'<text x="{x_at(i):.1f}" y="{h - 8}" class="axis" text-anchor="middle">{html.escape(labels[i])}</text>')
    if hline:
        y = y_at(hline["value"])
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{w - right}" y2="{y:.1f}" class="thr"/>')
        parts.append(f'<text x="{left + 4}" y="{y - 4:.1f}" class="thr-label" text-anchor="start">{html.escape(hline["label"])}</text>')
    for s in series:
        vals = s["values"]
        if kind == "bar":
            bw = max(3.0, pw / n * 0.7)
            for i, v in enumerate(vals):
                if v:
                    y = y_at(v)
                    parts.append(
                        f'<rect x="{x_at(i) - bw / 2:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{top + ph - y:.1f}" rx="2" fill="{s["color"]}">'
                        f'<title>{html.escape(labels[i])} · {html.escape(s["name"])}: {_fmt(v, digits)} {html.escape(unit)}</title></rect>')
            continue
        segment: list[str] = []
        for i, v in enumerate(vals + [None]):
            if v is None:
                if len(segment) > 1:
                    dash = f' stroke-dasharray="{s["dash"]}"' if s.get("dash") else ""
                    parts.append(f'<polyline points="{" ".join(segment)}" fill="none" stroke="{s["color"]}" stroke-width="2"{dash}/>')
                segment = []
            else:
                segment.append(f"{x_at(i):.1f},{y_at(v):.1f}")
        for i, v in enumerate(vals):
            if v is not None:
                parts.append(
                    f'<circle cx="{x_at(i):.1f}" cy="{y_at(v):.1f}" r="3.5" fill="{s["color"]}" class="dot">'
                    f'<title>{html.escape(labels[i])} · {html.escape(s["name"])}: {_fmt(v, digits)} {html.escape(unit)}</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


def legend(series: list[dict]) -> str:
    if len(series) < 2:
        return ""
    items = "".join(
        f'<span class="lg"><i style="background:{s["color"]}"></i>{html.escape(s["name"])}</span>' for s in series
    )
    return f'<div class="legend">{items}</div>'


# --------------------------------------------------------------------------- HTML
STATUS_TEXT = {"ok": "✔ Đạt ngưỡng", "breach": "▲ Vượt ngưỡng", "nodata": "… Chưa có dữ liệu"}
OPERATOR_TEXT = {"lte": "≤", "gte": "≥"}

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#111827;--muted:#4b5563;--grid:#e5e7eb;--thr:#6b7280;--ok:#166534;--okbg:#dcfce7;--bad:#991b1b;--badbg:#fee2e2;--nd:#374151;--ndbg:#e5e7eb}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1115;--card:#171a21;--ink:#f3f4f6;--muted:#9ca3af;--grid:#2a2f3a;--thr:#9ca3af;--ok:#86efac;--okbg:#14351f;--bad:#fca5a5;--badbg:#3f1616;--nd:#d1d5db;--ndbg:#2a2f3a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--muted);margin:0 0 16px}
.grid6{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--grid);border-radius:12px;padding:14px 16px}
.card h2{font-size:15px;margin:0}.unit{color:var(--muted);font-size:12px}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.chip{font-size:12px;padding:2px 8px;border-radius:999px;white-space:nowrap}
.chip.ok{color:var(--ok);background:var(--okbg)}.chip.breach{color:var(--bad);background:var(--badbg)}.chip.nodata{color:var(--nd);background:var(--ndbg)}
.stats{display:flex;flex-wrap:wrap;gap:6px 18px;margin:8px 0}.stat b{font-size:20px;font-variant-numeric:tabular-nums}.stat span{display:block;color:var(--muted);font-size:12px}
.thr-note{color:var(--muted);font-size:12px;margin:0 0 6px}
.chart{width:100%;height:auto}.grid{stroke:var(--grid);stroke-width:1}.axis{fill:var(--muted);font-size:10px}
.thr{stroke:var(--thr);stroke-width:1.5;stroke-dasharray:5 4}.thr-label{fill:var(--muted);font-size:10px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted)}.lg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
.empty{padding:36px 0;text-align:center;color:var(--muted)}details{margin-top:8px;font-size:12px;color:var(--muted)}
table{border-collapse:collapse;width:100%;margin-top:6px}td,th{border-bottom:1px solid var(--grid);padding:3px 6px;text-align:right;font-variant-numeric:tabular-nums}th:first-child,td:first-child{text-align:left}
"""


def _stat(label: str, value: str) -> str:
    return f'<div class="stat"><b>{value}</b><span>{html.escape(label)}</span></div>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<details><summary>Xem dạng bảng</summary><table><tr>{head}</tr>{body}</table></details>'


def build_panel(panel: dict, m: dict, labels: list[str], guard: dict) -> str:
    pid, per = panel["id"], m["per"]
    thr = panel["threshold"]
    unit = panel["unit"]
    value = threshold_value(pid, thr["aggregation"], m)
    status = evaluate(value, thr["operator"], thr["value"])
    limit_text = f'{thr["aggregation"]} {OPERATOR_TEXT[thr["operator"]]} {thr["value"]:g} {unit}'
    stats = chart = table = ""
    hline = None
    if pid == "latency":
        L = m["latency"]
        stats = "".join(_stat(k, _fmt(v) + " ms") for k, v in
                        (("P50", L["p50"]), ("P95", L["p95"]), ("P99", L["p99"]), ("TTFT P95", L["ttft_p95"])))
        series = [
            {"name": "P50", "values": [_pct(s["lat"], 50) for s in per], "color": SERIES_COLORS["p50"]},
            {"name": "P95", "values": [_pct(s["lat"], 95) for s in per], "color": SERIES_COLORS["p95"], "dash": "6 3"},
            {"name": "P99", "values": [_pct(s["lat"], 99) for s in per], "color": SERIES_COLORS["p99"], "dash": "2 3"},
            {"name": "TTFT P95", "values": [_pct(s["ttft"], 95) for s in per], "color": SERIES_COLORS["ttft"], "dash": "10 3 2 3"},
        ]
        hline = {"value": thr["value"], "label": f'Ngưỡng P95 {thr["value"]:g} ms'}
        chart = chart_svg(labels, series, "line", hline, "ms") + legend(series)
        table = _table(["Phút", "P50", "P95", "P99", "TTFT P95"],
                       [[labels[i], _fmt(series[0]["values"][i]), _fmt(series[1]["values"][i]), _fmt(series[2]["values"][i]), _fmt(series[3]["values"][i])]
                        for i in range(len(labels)) if per[i]["lat"]])
    elif pid == "traffic":
        stats = _stat("Tổng request", _fmt(m["traffic"]["count"])) + _stat("Request/phút (khoảng hoạt động)", _fmt(m["traffic"]["rate_per_minute"], 1))
        series = [{"name": "Request", "values": [s["recv"] for s in per], "color": SERIES_COLORS["p50"]}]
        chart = chart_svg(labels, series, "bar", {"value": thr["value"], "label": f'Ngưỡng {thr["value"]:g} req/phút'}, "req")
        table = _table(["Phút", "Request"], [[labels[i], str(s["recv"])] for i, s in enumerate(per) if s["recv"]])
    elif pid == "errors":
        E = m["errors"]
        retr_min = guard.get("retrieval_success_rate_pct_min")
        stats = (_stat("Error rate", _fmt(E["error_rate_pct"], 2) + " %")
                 + _stat("Request lỗi / tổng", f'{E["failed"]} / {E["received"]}')
                 + _stat("Retrieval success" + (f" (mục tiêu ≥ {retr_min:g}%)" if retr_min else ""),
                         _fmt(E["tool_success_rate_pct"], 1) + " %"))
        rate = [(s["fail"] / s["recv"] * 100) if s["recv"] else None for s in per]
        series = [{"name": "Error rate %", "values": rate, "color": SERIES_COLORS["p95"]}]
        chart = chart_svg(labels, series, "line", {"value": thr["value"], "label": f'Ngưỡng {thr["value"]:g}%'}, "%", 1)
        rows = [[k, str(v)] for k, v in sorted(E["breakdown"].items())] or [["(không có lỗi)", "0"]]
        table = _table(["error_type", "Số lượng"], rows)
    elif pid == "cost":
        C = m["cost"]
        stats = _stat("Tổng chi phí", "$" + _fmt(C["total"], 4)) + _stat("Trung bình/request", "$" + _fmt(C["avg"], 5))
        series = [{"name": "USD/phút", "values": [s["cost"] or None for s in per], "color": SERIES_COLORS["p99"]}]
        chart = chart_svg(labels, series, "bar", None, "USD", 4)
        table = _table(["Phút", "USD"], [[labels[i], f'{s["cost"]:.5f}'] for i, s in enumerate(per) if s["cost"]])
    elif pid == "tokens":
        T = m["tokens"]
        stats = _stat("Input tokens", _fmt(T["in"])) + _stat("Output tokens", _fmt(T["out"]))
        series = [
            {"name": "Input", "values": [s["tin"] or None for s in per], "color": SERIES_COLORS["p50"]},
            {"name": "Output", "values": [s["tout"] or None for s in per], "color": SERIES_COLORS["p95"], "dash": "6 3"},
        ]
        chart = chart_svg(labels, series, "line", None, "tokens") + legend(series)
        table = _table(["Phút", "Input", "Output"], [[labels[i], str(s["tin"]), str(s["tout"])] for i, s in enumerate(per) if s["tin"] or s["tout"]])
    elif pid == "quality":
        stats = _stat("Quality trung bình", _fmt(m["quality"]["mean"], 2))
        series = [{"name": "Quality", "values": [_mean(s["q"]) for s in per], "color": SERIES_COLORS["ttft"]}]
        chart = chart_svg(labels, series, "line", {"value": thr["value"], "label": f'Ngưỡng {thr["value"]:g}'}, "điểm", 2)
        table = _table(["Phút", "Quality"], [[labels[i], _fmt(_mean(s["q"]), 2)] for i, s in enumerate(per) if s["q"]])

    return (
        f'<section class="card" id="panel-{pid}"><div class="head"><div><h2>{html.escape(panel["title"])}</h2>'
        f'<span class="unit">Đơn vị: {html.escape(unit)} · 60 phút gần nhất</span></div>'
        f'<span class="chip {status}">{STATUS_TEXT[status]}</span></div>'
        f'<p class="thr-note">Ngưỡng: {html.escape(limit_text)} · giá trị hiện tại: {_fmt(value, 2)}</p>'
        f'<div class="stats">{stats}</div>{chart}{table}</section>'
    )


def render(config: dict, m: dict, end: datetime, tz: timezone, guard: dict, refresh: int | None) -> str:
    dash = config["dashboard"]
    labels = [b.astimezone(tz).strftime("%H:%M") for b in m["buckets"]]
    cards = "".join(build_panel(p, m, labels, guard) for p in dash["panels"])
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    stamp = end.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
    return (
        f'<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{meta}'
        f'<title>{html.escape(dash["title"])}</title><style>{CSS}</style></head><body><main>'
        f'<h1>{html.escape(dash["title"])}</h1>'
        f'<p class="sub">Nguồn: data/logs.jsonl · cửa sổ {dash["time_range_minutes"]} phút kết thúc lúc {stamp} (UTC{tz.utcoffset(None).total_seconds() / 3600:+g}) · '
        f'làm mới mỗi {dash["refresh_seconds"]} giây · trục thời gian theo phút</p>'
        f'<div class="grid6">{cards}</div></main></body></html>'
    )


def build(log: Path, config_path: Path, slo_path: Path, out: Path, end_mode: str, tz_hours: float, watch: bool) -> Path:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    guard: dict = {}
    if slo_path.exists():
        guard = (yaml.safe_load(slo_path.read_text(encoding="utf-8")) or {}).get("guardrails", {}) or {}
    records = load_records(log)
    if end_mode == "now" or not records:
        end = datetime.now(timezone.utc)
    else:
        end = max(r["_t"] for r in records)
    minutes = config["dashboard"]["time_range_minutes"]
    m = compute(records, end, minutes)
    tz = timezone(timedelta(hours=tz_hours))
    refresh = config["dashboard"]["refresh_seconds"] if watch else None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(config, m, end, tz, guard, refresh), encoding="utf-8")
    return out


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Dựng dashboard 6 panel từ structured log")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--slo", type=Path, default=DEFAULT_SLO)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--end", choices=["latest", "now"], default="latest",
                        help="latest: kết thúc tại log mới nhất (mặc định); now: giờ hiện tại")
    parser.add_argument("--tz", type=float, default=7.0, help="Múi giờ hiển thị (giờ lệch UTC), mặc định 7")
    parser.add_argument("--watch", action="store_true", help="Dựng lại liên tục theo refresh_seconds")
    args = parser.parse_args()

    while True:
        out = build(args.log, args.config, args.slo, args.out, args.end, args.tz, args.watch)
        print(f"Đã ghi {out}")
        if not args.watch:
            return 0
        time.sleep(yaml.safe_load(args.config.read_text(encoding="utf-8"))["dashboard"]["refresh_seconds"])


if __name__ == "__main__":
    raise SystemExit(main())
