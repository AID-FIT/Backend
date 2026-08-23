import asyncio

from scripts.run_agent_chat import LocalChatSession, format_agent_response, format_trace


class FakePipeline:
    def __init__(self, traces: list[dict]) -> None:
        self.traces = traces
        self.calls: list[dict] = []

    async def run(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.traces.pop(0)


def response(message: str) -> dict:
    return {
        "status": "success",
        "message": message,
        "recommendations": [],
        "style_guide": None,
    }


def test_local_chat_session_keeps_history_and_latest_rag_context() -> None:
    rag_item = {"item_id": "item_1", "source": "musinsa", "name": "테스트 팬츠"}
    pipeline = FakePipeline(
        [
            {
                "response": response("첫 답변"),
                "rag_items": [rag_item],
                "candidate_pool": [rag_item],
                "shown_item_refs": ["item_1"],
                "rag_query": "와이드 팬츠",
                "retrieval_target": "musinsa",
            },
            {
                "response": response("후속 답변"),
                "rag_items": [rag_item],
                "candidate_pool": [rag_item],
                "shown_item_refs": ["item_1"],
                "resolved_query": "더 저렴한 와이드 팬츠",
                "retrieval_target": "musinsa",
                "rag_reused": True,
            },
        ]
    )
    session = LocalChatSession(pipeline)  # type: ignore[arg-type]

    asyncio.run(session.send("와이드 팬츠 추천해줘"))
    asyncio.run(session.send("그중 더 저렴한 건?"))

    assert pipeline.calls[0]["chat_history"] == []
    assert pipeline.calls[1]["chat_history"] == [
        {"role": "user", "content": "와이드 팬츠 추천해줘"},
        {"role": "assistant", "content": "첫 답변"},
    ]
    assert pipeline.calls[1]["previous_rag_results"] == [rag_item]
    assert pipeline.calls[1]["previous_shown_item_refs"] == ["item_1"]
    assert pipeline.calls[1]["previous_rag_query"] == "와이드 팬츠"
    assert pipeline.calls[1]["previous_retrieval_target"] == "musinsa"
    assert session.previous_rag_query == "와이드 팬츠"


def test_reset_clears_all_in_memory_context() -> None:
    pipeline = FakePipeline([])
    session = LocalChatSession(pipeline)  # type: ignore[arg-type]
    session.chat_history = [{"role": "user", "content": "질문"}]
    session.previous_rag_results = [{"item_id": "item_1"}]
    session.previous_shown_item_refs = ["item_1"]
    session.previous_rag_query = "query"
    session.previous_retrieval_target = "musinsa"

    session.reset()

    assert session.chat_history == []
    assert session.previous_rag_results == []
    assert session.previous_shown_item_refs == []
    assert session.previous_rag_query is None
    assert session.previous_retrieval_target is None


def test_console_formatters_show_recommendation_and_trace_summary() -> None:
    formatted = format_agent_response(
        {
            "message": "추천 결과입니다.",
            "recommendations": [
                {
                    "brand": "브랜드",
                    "item_name": "팬츠",
                    "price": 59000,
                    "reason": "잘 어울립니다.",
                    "product_url": "https://example.com/item",
                }
            ],
            "style_guide": {"summary": "미니멀 룩", "tips": ["검정 신발을 매치하세요."]},
        }
    )
    trace = format_trace({"intent": "fashion_service", "rag_items": [{"item_id": "1"}]})

    assert "브랜드 팬츠 · 59,000원" in formatted
    assert "스타일 가이드: 미니멀 룩" in formatted
    assert '"intent": "fashion_service"' in trace
    assert '"rag_item_count": 1' in trace
