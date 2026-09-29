from __future__ import annotations

import re
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from structlog.contextvars import bind_contextvars, clear_contextvars

# Chỉ chấp nhận ID an toàn để tránh log injection (xuống dòng, ngoặc kép, JSON lạ).
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def new_correlation_id() -> str:
    return f"req-{uuid.uuid4().hex[:8]}"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Xóa context của request trước để không rò correlation_id/user giữa các request.
        clear_contextvars()

        # Nhận x-request-id từ client nếu hợp lệ, nếu không thì sinh mới: req-<8 hex>.
        incoming = request.headers.get("x-request-id", "").strip()
        correlation_id = (
            incoming if _VALID_REQUEST_ID.fullmatch(incoming) else new_correlation_id()
        )

        # Mọi log.info(...) trong request này sẽ tự có correlation_id.
        bind_contextvars(correlation_id=correlation_id)
        request.state.correlation_id = correlation_id

        start = time.perf_counter()
        response = await call_next(request)

        response.headers["x-request-id"] = correlation_id
        response.headers["x-response-time-ms"] = str(int((time.perf_counter() - start) * 1000))

        return response
