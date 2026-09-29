from __future__ import annotations

import os
import time
from dataclasses import dataclass

from . import metrics
from .mock_llm import FakeLLM
from .mock_rag import retrieve
from .pii import hash_user_id, scrub_text, summarize_text
from .prompt_management import resolve_prompt
from .tracing import get_langfuse_client, observe, propagate_attributes, tracing_enabled


def _safe_update(client, method: str, **kwargs) -> None:
    """Gọi client.update_current_* nếu có; telemetry không bao giờ được làm hỏng request."""
    update = getattr(client, method, None)
    if callable(update):
        try:
            update(**kwargs)
        except Exception:  # pragma: no cover - lỗi Langfuse không được ảnh hưởng người dùng
            pass


@dataclass
class AgentResult:
    answer: str
    latency_ms: int
    ttft_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    quality_score: float


class LabAgent:
    def __init__(self, model: str = "claude-sonnet-4-5") -> None:
        self.model = model
        self.llm = FakeLLM(model=model)

    @observe(name="lab-agent-run", as_type="agent", capture_input=False, capture_output=False)
    def run(
        self,
        user_id: str,
        feature: str,
        session_id: str,
        message: str,
        correlation_id: str,
    ) -> AgentResult:
        langfuse_client = get_langfuse_client()
        with propagate_attributes(
            user_id=hash_user_id(user_id),
            session_id=session_id,
            tags=["lab", feature, self.model],
            trace_name="day13-agent-request",
            environment=os.getenv("APP_ENV", "dev"),
            metadata={
                "feature": feature,
                "model": self.model,
                "correlation_id": correlation_id,
            },
        ):
            started = time.perf_counter()
            docs = self._retrieve(langfuse_client, message)
            prompt = resolve_prompt(
                langfuse_client,
                feature=feature,
                docs=docs,
                message=message,
                enabled=tracing_enabled(),
            )
            langfuse_client.update_current_span(
                metadata={
                    "doc_count": len(docs),
                    "query_preview": summarize_text(message),
                    "prompt_name": prompt.name,
                    "prompt_label": prompt.label,
                    "prompt_version": prompt.version,
                    "prompt_source": prompt.source,
                    "prompt_fetch_error": prompt.fetch_error or "",
                },
                version=prompt.version,
            )
            # Generation là child của root span; prompt managed được gắn qua propagate_attributes
            # để Langfuse liên kết trace với đúng prompt name/version.
            with propagate_attributes(prompt=prompt.managed_prompt):
                response = self._generate(langfuse_client, prompt.text)
            quality_score = self._heuristic_quality(message, response.text, docs)
            latency_ms = int((time.perf_counter() - started) * 1000)
            cost_usd = self._estimate_cost(response.usage.input_tokens, response.usage.output_tokens)

        metrics.record_request(
            latency_ms=latency_ms,
            ttft_ms=response.ttft_ms,
            cost_usd=cost_usd,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            quality_score=quality_score,
        )

        return AgentResult(
            answer=response.text,
            latency_ms=latency_ms,
            ttft_ms=response.ttft_ms,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            cost_usd=cost_usd,
            quality_score=quality_score,
        )

    @observe(name="retrieval", as_type="retriever", capture_input=False, capture_output=False)
    def _retrieve(self, client, message: str) -> list[str]:
        # Không capture input/output thô: message có thể chứa PII, chỉ ghi bản đã scrub.
        _safe_update(client, "update_current_span", input=summarize_text(message))
        docs = retrieve(message)  # nếu lỗi (tool_fail), @observe tự đánh dấu span ERROR
        _safe_update(
            client,
            "update_current_span",
            output={"doc_count": len(docs)},
            metadata={"doc_count": len(docs), "matched": not docs[0].startswith("No domain document")},
        )
        return docs

    @observe(name="llm-generate", as_type="generation", capture_input=False, capture_output=False)
    def _generate(self, client, prompt_text: str):
        response = self.llm.generate(prompt_text)
        input_cost, output_cost = self._cost_breakdown(
            response.usage.input_tokens, response.usage.output_tokens
        )
        _safe_update(
            client,
            "update_current_generation",
            model=response.model,
            # Prompt/answer được scrub trước khi gửi lên Langfuse.
            input=scrub_text(prompt_text),
            output=scrub_text(response.text),
            usage_details={
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
                "total": response.usage.input_tokens + response.usage.output_tokens,
            },
            cost_details={
                "input": input_cost,
                "output": output_cost,
                "total": round(input_cost + output_cost, 6),
            },
            metadata={"ttft_ms": response.ttft_ms},
        )
        return response

    def _cost_breakdown(self, tokens_in: int, tokens_out: int) -> tuple[float, float]:
        return (tokens_in / 1_000_000) * 3, (tokens_out / 1_000_000) * 15

    def _estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        input_cost, output_cost = self._cost_breakdown(tokens_in, tokens_out)
        return round(input_cost + output_cost, 6)

    def _heuristic_quality(self, question: str, answer: str, docs: list[str]) -> float:
        score = 0.5
        if docs:
            score += 0.2
        if len(answer) > 40:
            score += 0.1
        if question.lower().split()[0:1] and any(token in answer.lower() for token in question.lower().split()[:3]):
            score += 0.1
        if "[REDACTED" in answer:
            score -= 0.2
        return round(max(0.0, min(1.0, score)), 2)
