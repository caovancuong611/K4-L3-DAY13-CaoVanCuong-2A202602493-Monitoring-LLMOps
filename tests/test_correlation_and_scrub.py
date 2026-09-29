from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx

from app import logging_config
from app.logging_config import scrub_event
from app.main import app

BODY = {
    "user_id": "student-01",
    "session_id": "session-01",
    "feature": "qa",
    "message": "My email is student@vinuni.edu.vn and phone 0987654321, card 4111 1111 1111 1111",
}


def _post(headers: dict[str, str] | None = None, body: dict | None = None) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/chat", json=body or BODY, headers=headers or {})

    return asyncio.run(go())


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_generates_valid_correlation_id_and_returns_it(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logging_config, "LOG_PATH", tmp_path / "logs.jsonl")
    response = _post()
    cid = response.headers["x-request-id"]
    assert re.fullmatch(r"req-[0-9a-f]{8}", cid)
    assert response.json()["correlation_id"] == cid
    assert int(response.headers["x-response-time-ms"]) >= 0


def test_incoming_request_id_is_reused_and_logged(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    response = _post({"x-request-id": "req-abcdef12"})
    assert response.headers["x-request-id"] == "req-abcdef12"
    events = _events(log_path)
    assert events and all(e["correlation_id"] == "req-abcdef12" for e in events)


def test_unsafe_request_id_is_replaced(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logging_config, "LOG_PATH", tmp_path / "logs.jsonl")
    response = _post({"x-request-id": "bad id\"}{"})
    assert re.fullmatch(r"req-[0-9a-f]{8}", response.headers["x-request-id"])


def test_logs_are_enriched_and_do_not_leak_between_requests(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    _post({"x-request-id": "req-00000001"})
    _post({"x-request-id": "req-00000002"}, body={**BODY, "user_id": "other", "session_id": "session-02"})
    api = [e for e in _events(log_path) if e.get("service") == "api"]
    for field in ("user_id_hash", "session_id", "feature", "model", "env"):
        assert all(field in e for e in api), field
    second = [e for e in api if e["correlation_id"] == "req-00000002"]
    assert second and all(e["session_id"] == "session-02" for e in second)


def test_pii_never_reaches_the_log_file(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    _post()
    raw = log_path.read_text(encoding="utf-8")
    for secret in ("student@vinuni", "0987654321", "4111 1111 1111 1111"):
        assert secret not in raw
    assert "REDACTED_" in raw


def test_scrub_event_handles_nested_payload_and_keeps_ids() -> None:
    out = scrub_event(
        None,
        "info",
        {
            "event": "x",
            "user_id_hash": "123456789012",
            "payload": {"a": ["mail me: a@b.co"], "n": 3},
            "exception": "boom for 0987654321",
        },
    )
    assert out["user_id_hash"] == "123456789012"
    assert "a@b.co" not in str(out["payload"])
    assert out["payload"]["n"] == 3
    assert "0987654321" not in out["exception"]
