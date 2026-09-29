from __future__ import annotations

from app import agent as agent_module


class Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def update_current_span(self, **kwargs) -> None:
        self.calls.append(("span", kwargs))

    def update_current_generation(self, **kwargs) -> None:
        self.calls.append(("generation", kwargs))


def test_generation_gets_model_usage_cost_and_scrubbed_io() -> None:
    client = Client()
    agent = agent_module.LabAgent()
    response = agent._generate.__wrapped__(agent, client, "Question=mail a@b.co phone 0987654321")

    kind, update = client.calls[-1]
    assert kind == "generation"
    assert update["model"] == agent.model
    assert update["usage_details"]["input"] == response.usage.input_tokens
    assert update["usage_details"]["total"] == (
        response.usage.input_tokens + response.usage.output_tokens
    )
    assert update["cost_details"]["total"] > 0
    assert "a@b.co" not in update["input"] and "0987654321" not in update["input"]


def test_retriever_records_doc_count_without_raw_message() -> None:
    client = Client()
    agent = agent_module.LabAgent()
    docs = agent._retrieve.__wrapped__(agent, client, "refund for a@b.co")

    assert docs
    assert "a@b.co" not in str(client.calls)
    assert client.calls[-1][1]["metadata"]["doc_count"] == len(docs)


def test_missing_client_methods_do_not_break_the_request() -> None:
    agent = agent_module.LabAgent()
    assert agent._retrieve.__wrapped__(agent, object(), "monitoring")
