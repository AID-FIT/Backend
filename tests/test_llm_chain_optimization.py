import asyncio
import json

import pytest

from app.agent.nodes import AgentNodes
from app.core import config as config_module
from app.services.llm_service import MAX_RECOMMENDATIONS, LlmService
from tests.test_agent_pipeline import FakeLlmService


class CapturingClient:
    """generateContent 요청의 URL과 payload만 붙잡는다."""

    captured: list[dict] = []

    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):  # noqa: A002
        CapturingClient.captured.append({"url": url, "payload": json})
        return FakeResponse()


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "candidates": [
                {"content": {"parts": [{"text": '{"intent": "fashion_service", "reason": "ok"}'}]}}
            ]
        }


@pytest.fixture(autouse=True)
def _capture_requests(monkeypatch):
    CapturingClient.captured = []
    monkeypatch.setattr("app.services.llm_service.httpx.AsyncClient", CapturingClient)
    monkeypatch.setattr(config_module.settings, "gemini_api_key", "test-key", raising=False)
    monkeypatch.setattr(config_module.settings, "gemini_model", "heavy-model", raising=False)
    return CapturingClient


def generate(**kwargs) -> None:
    service = LlmService(use_mock_ai=False)
    asyncio.run(
        service._generate_structured(
            system_instruction="classify",
            prompt={"q": "바지"},
            response_schema={"type": "OBJECT"},
            temperature=0.0,
            **kwargs,
        )
    )


def last_request() -> dict:
    return CapturingClient.captured[-1]


def test_thinking_budget_is_sent_when_given() -> None:
    generate(thinking_budget=0)

    config = last_request()["payload"]["generationConfig"]
    assert config["thinkingConfig"] == {"thinkingBudget": 0}


def test_thinking_config_is_absent_when_not_given() -> None:
    # 설정하지 않은 환경에서는 기존 동작 그대로여야 한다.
    generate()

    assert "thinkingConfig" not in last_request()["payload"]["generationConfig"]


def test_model_override_changes_the_endpoint() -> None:
    generate(model="light-model")

    assert "/models/light-model:generateContent" in last_request()["url"]


def test_default_model_is_used_without_an_override() -> None:
    generate()

    assert "/models/heavy-model:generateContent" in last_request()["url"]


def test_fast_model_falls_back_to_the_default_when_unset(monkeypatch) -> None:
    monkeypatch.setattr(config_module.settings, "llm_fast_model", "", raising=False)

    assert config_module.settings.fast_model_name == "heavy-model"


def test_fast_model_is_used_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(config_module.settings, "llm_fast_model", "light-model", raising=False)

    assert config_module.settings.fast_model_name == "light-model"


def test_intent_classification_runs_on_the_fast_model(monkeypatch) -> None:
    monkeypatch.setattr(config_module.settings, "llm_fast_model", "light-model", raising=False)
    monkeypatch.setattr(config_module.settings, "llm_fast_thinking_budget", 0, raising=False)

    asyncio.run(LlmService(use_mock_ai=False).classify_intent(query="바지 추천"))

    request = last_request()
    assert "/models/light-model:" in request["url"]
    assert request["payload"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


def test_query_refinement_runs_on_the_fast_model(monkeypatch) -> None:
    monkeypatch.setattr(config_module.settings, "llm_fast_model", "light-model", raising=False)
    monkeypatch.setattr(
        FakeResponse,
        "json",
        lambda self: {
            "candidates": [{"content": {"parts": [{"text": '{"query": "검은 바지"}'}]}}]
        },
    )

    asyncio.run(LlmService(use_mock_ai=False).refine_query(query="바지"))

    assert "/models/light-model:" in last_request()["url"]


def test_final_composition_keeps_the_default_model(monkeypatch) -> None:
    # 사용자가 읽는 문장을 만드는 호출이라 추론과 모델을 그대로 둔다.
    monkeypatch.setattr(config_module.settings, "llm_fast_model", "light-model", raising=False)
    monkeypatch.setattr(
        FakeResponse,
        "json",
        lambda self: {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "status": "empty",
                                        "message": "결과 없음",
                                        "recommendations": [],
                                        "style_guide": None,
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        },
    )

    asyncio.run(
        LlmService(use_mock_ai=False).compose_recommendation(
            query="바지",
            vlm_items=[],
            ranked_items=[
                {
                    "item_id": "musinsa_1",
                    "source": "musinsa",
                    "name": "슬랙스",
                    "brand": "브랜드",
                    "category": "바지",
                    "image_url": "https://image.example/a.jpg",
                    "product_url": "https://www.musinsa.com/products/1",
                    "price": 10000,
                }
            ],
        )
    )

    request = last_request()
    assert "/models/heavy-model:" in request["url"]
    assert "thinkingConfig" not in request["payload"]["generationConfig"]


def test_query_refiner_skips_llm_without_history_or_vlm() -> None:
    llm = FakeLlmService()
    nodes = AgentNodes(llm_service=llm)
    query = "와이드 슬랙스 바지 추천해줘"

    result = asyncio.run(
        nodes.query_refiner_node(
            {"query": query, "chat_history": [], "vlm_items": []}
        )
    )

    assert llm.refine_calls == []
    assert result["resolved_query"] == query


def test_query_refiner_calls_llm_when_history_exists() -> None:
    refined = "검은색 재킷에 어울리는 더 저렴한 회색 와이드 슬랙스"
    llm = FakeLlmService(refined_query=refined)
    nodes = AgentNodes(llm_service=llm)

    result = asyncio.run(
        nodes.query_refiner_node(
            {
                "query": "더 저렴한 걸로 다시 골라줘",
                "chat_history": [
                    {"role": "user", "content": "와이드 슬랙스 바지 추천해줘"},
                    {"role": "assistant", "content": "회색 와이드 슬랙스를 추천합니다."},
                ],
                "vlm_items": [],
            }
        )
    )

    assert len(llm.refine_calls) == 1
    assert result["resolved_query"] == refined


def test_query_refiner_calls_llm_when_vlm_items_exist() -> None:
    llm = FakeLlmService(refined_query="화이트 니트에 어울리는 블랙 와이드 팬츠")
    nodes = AgentNodes(llm_service=llm)

    result = asyncio.run(
        nodes.query_refiner_node(
            {
                "query": "이거랑 어울리는 바지",
                "chat_history": [],
                "vlm_items": [{"category": "상의", "color": "white", "material": "knit"}],
            }
        )
    )

    assert len(llm.refine_calls) == 1
    assert llm.refine_calls[0]["vlm_items"][0]["material"] == "knit"
    assert result["resolved_query"] == "화이트 니트에 어울리는 블랙 와이드 팬츠"


class RecordingLlmService(LlmService):
    """compose_recommendation이 만든 payload만 붙잡는다."""

    def __init__(self) -> None:
        super().__init__()
        self.payload: dict | None = None

    def _build_gemini_payload(self, *args, **kwargs):  # type: ignore[override]
        self.payload = super()._build_gemini_payload(*args, **kwargs)
        return self.payload


def ranked(count: int) -> list[dict]:
    return [
        {
            "item_id": f"item_{index}",
            "source": "musinsa",
            "item_name": f"상품 {index}",
            "category": "상의",
            "image_url": f"https://image.example/{index}.jpg",
            "product_url": f"https://www.musinsa.com/products/{index}",
            "final_score": 1.0 - index * 0.01,
        }
        for index in range(count)
    ]


def test_mock_recommendation_honors_the_requested_count() -> None:
    service = LlmService()
    service.use_mock_ai = True

    response = asyncio.run(
        service.compose_recommendation(
            "오늘 뭐 입지", [], ranked(12), "musinsa", max_recommendations=7
        )
    )

    assert len(response["recommendations"]) == 7


def test_recommendation_defaults_to_the_chat_cap() -> None:
    service = LlmService()
    service.use_mock_ai = True

    response = asyncio.run(
        service.compose_recommendation("오늘 뭐 입지", [], ranked(12), "musinsa")
    )

    assert len(response["recommendations"]) == MAX_RECOMMENDATIONS


def test_prompt_carries_the_target_count_and_enough_candidates() -> None:
    service = RecordingLlmService()

    payload = service._build_gemini_payload(
        "오늘 뭐 입지", [], ranked(30), "musinsa", max_recommendations=7
    )
    prompt = json.loads(payload["contents"][0]["parts"][0]["text"])

    assert prompt["target_recommendation_count"] == 7
    # 7개를 고르라면서 후보를 8개만 주면 사실상 선택지가 없다.
    assert len(prompt["candidate_items"]) >= 14
