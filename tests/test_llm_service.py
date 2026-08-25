import asyncio
import json

from app.services import llm_service as llm_module
from app.services.llm_service import LlmService
from tests.fake_ai import DeterministicLlmService


class FakeGeminiResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(self.payload, ensure_ascii=False),
                            }
                        ]
                    }
                }
            ]
        }


class FakeAsyncClient:
    calls: list[dict] = []
    response_payload: dict = {}

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, headers: dict, json: dict) -> FakeGeminiResponse:
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": self.timeout})
        return FakeGeminiResponse(self.response_payload)


def test_external_compose_calls_gemini_and_normalizes_candidates(monkeypatch) -> None:
    FakeAsyncClient.calls = []
    FakeAsyncClient.response_payload = {
        "status": "success",
        "message": "화이트 니트에 맞는 추천입니다.",
        "recommendations": [
            {
                "item_id": "item_1",
                "source": "musinsa",
                "image_url": "https://wrong.example/image.jpg",
                "reason": "밝은 상의와 자연스럽게 이어집니다.",
            }
        ],
        "style_guide": {"summary": "미니멀 캐주얼", "tips": ["톤을 밝게 맞추세요."]},
    }
    monkeypatch.setattr(llm_module.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm_module.settings, "gemini_model", "gemini-test")
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", FakeAsyncClient)

    service = LlmService()
    result = asyncio.run(
        service.compose_recommendation(
            query="화이트 니트에 어울리는 바지 추천",
            vlm_items=[{"category": "top", "color": "white"}],
            ranked_items=[
                {
                    "item_id": "item_1",
                    "source": "musinsa",
                    "name": "Straight Denim Pants",
                    "brand": "Example",
                    "category": "pants",
                    "image_url": "https://image.example/item_1.jpg",
                    "product_url": "https://www.musinsa.com/products/item_1",
                    "price": 59000,
                }
            ],
            closet_items=[{"closet_item_id": "closet_001", "color": "black"}],
            use_closet_style=True,
            user_profile={"preferred_styles": ["minimal"]},
        )
    )

    assert result["status"] == "success"
    assert result["recommendations"][0]["image_url"] == "https://image.example/item_1.jpg"
    assert result["recommendations"][0]["product_url"] == "https://www.musinsa.com/products/item_1"
    assert FakeAsyncClient.calls[0]["headers"]["x-goog-api-key"] == "test-key"
    assert FakeAsyncClient.calls[0]["json"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseSchema" in FakeAsyncClient.calls[0]["json"]["generationConfig"]
    prompt = json.loads(FakeAsyncClient.calls[0]["json"]["contents"][0]["parts"][0]["text"])
    assert prompt["closet_items"][0]["closet_item_id"] == "closet_001"
    assert prompt["use_closet_style"] is True
    assert prompt["user_profile"] == {"preferred_styles": ["minimal"]}


def test_external_compose_returns_empty_when_gemini_recommends_unknown_item(monkeypatch) -> None:
    FakeAsyncClient.calls = []
    FakeAsyncClient.response_payload = {
        "status": "success",
        "message": "추천 결과입니다.",
        "recommendations": [
            {
                "item_id": "unknown",
                "source": "musinsa",
                "image_url": "https://image.example/unknown.jpg",
                "reason": "잘 어울립니다.",
            }
        ],
        "style_guide": {"summary": "캐주얼", "tips": []},
    }
    monkeypatch.setattr(llm_module.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", FakeAsyncClient)

    service = LlmService()
    result = asyncio.run(
        service.compose_recommendation(
            query="추천해줘",
            vlm_items=[],
            ranked_items=[
                {
                    "item_id": "known",
                    "source": "musinsa",
                    "name": "Known Item",
                    "image_url": "https://image.example/known.jpg",
                    "product_url": "https://www.musinsa.com/products/known",
                }
            ],
        )
    )

    assert result["status"] == "empty"
    assert result["recommendations"] == []


def test_external_intent_and_query_refinement_use_structured_gemini(monkeypatch) -> None:
    FakeAsyncClient.calls = []
    monkeypatch.setattr(llm_module.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", FakeAsyncClient)
    service = LlmService()

    FakeAsyncClient.response_payload = {
        "intent": "fashion_service",
        "reason": "image-based styling request",
    }
    intent = asyncio.run(
        service.classify_intent(
            query="이 사진에 어울리는 바지",
            chat_history=[],
            has_image=True,
        )
    )

    FakeAsyncClient.response_payload = {
        "query": "화이트 오버핏 니트에 어울리는 봄 미니멀 팬츠",
    }
    refined = asyncio.run(
        service.refine_query(
            query="이거랑 어울리는 바지",
            chat_history=[],
            vlm_items=[{"category": "knit", "color": "white", "fit": "oversized"}],
        )
    )

    assert intent["intent"] == "fashion_service"
    assert refined == "화이트 오버핏 니트에 어울리는 봄 미니멀 팬츠"
    assert len(FakeAsyncClient.calls) == 2
    assert FakeAsyncClient.calls[0]["json"]["generationConfig"]["temperature"] == 0.0
    refine_prompt = json.loads(FakeAsyncClient.calls[1]["json"]["contents"][0]["parts"][0]["text"])
    assert refine_prompt["vlm_items"][0]["color"] == "white"


def test_external_retrieval_plan_cannot_reuse_unknown_candidate(monkeypatch) -> None:
    FakeAsyncClient.calls = []
    FakeAsyncClient.response_payload = {
        "action": "reuse",
        "retrieval_target": "musinsa",
        "candidate_scope": "all",
        "selected_item_refs": ["invented-item"],
        "reason": "invalid model selection",
    }
    monkeypatch.setattr(llm_module.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", FakeAsyncClient)

    service = LlmService()
    plan = asyncio.run(
        service.plan_retrieval(
            query="직전 후보 중 저렴한 상품",
            original_query="그중 저렴한 걸로",
            previous_rag_results=[
                {
                    "item_id": "known-item",
                    "source": "musinsa",
                    "name": "Known Item",
                    "image_url": "https://image.example/known.jpg",
                    "product_url": "https://www.musinsa.com/products/known-item",
                    "price": 39000,
                }
            ],
            previous_retrieval_target="musinsa",
        )
    )

    assert plan["action"] == "retrieve"
    assert plan["selected_item_refs"] == []


def test_mock_retrieval_plan_reuses_only_unseen_candidates_for_more_results() -> None:
    service = DeterministicLlmService()
    previous_items = [
        {
            "item_id": item_id,
            "source": "musinsa",
            "name": f"Item {item_id}",
            "category": "pants",
            "image_url": f"https://image.example/{item_id}.jpg",
            "product_url": f"https://www.musinsa.com/products/{item_id}",
        }
        for item_id in ("shown", "unseen-1", "unseen-2")
    ]

    plan = asyncio.run(
        service.plan_retrieval(
            query="기존 요청과 비슷한 팬츠를 하나 더 추천",
            original_query="비슷한 느낌으로 하나 더 보여줘",
            previous_rag_results=previous_items,
            previous_shown_item_refs=["shown"],
            previous_retrieval_target="musinsa",
        )
    )

    assert plan["action"] == "reuse"
    assert plan["candidate_scope"] == "unseen"
    assert plan["selected_item_refs"] == ["unseen-1", "unseen-2"]


def test_mock_retrieval_plan_understands_generic_closet_wording() -> None:
    service = DeterministicLlmService()

    plan = asyncio.run(
        service.plan_retrieval(
            query="이 옷에 어울리는 옷을 옷장에서 찾아줘",
            original_query="이 옷에 어울리는 옷을 옷장에서 찾아줘",
            closet_items=[{"closet_item_id": "closet_001"}],
        )
    )

    assert plan["retrieval_target"] == "closet"


def test_mock_retrieval_plan_retrieves_when_unseen_cache_is_exhausted() -> None:
    service = DeterministicLlmService()
    previous_item = {
        "item_id": "shown",
        "source": "musinsa",
        "name": "Shown Item",
        "category": "pants",
        "image_url": "https://image.example/shown.jpg",
        "product_url": "https://www.musinsa.com/products/shown",
    }

    plan = asyncio.run(
        service.plan_retrieval(
            query="기존 요청과 비슷한 팬츠를 하나 더 추천",
            original_query="하나 더 보여줘",
            previous_rag_results=[previous_item],
            previous_shown_item_refs=["shown"],
            previous_retrieval_target="musinsa",
        )
    )

    assert plan["action"] == "retrieve"
    assert plan["candidate_scope"] == "unseen"
    assert plan["selected_item_refs"] == []


def test_external_retrieval_plan_filters_shown_refs_from_unseen_scope(monkeypatch) -> None:
    FakeAsyncClient.calls = []
    FakeAsyncClient.response_payload = {
        "action": "reuse",
        "retrieval_target": "musinsa",
        "candidate_scope": "unseen",
        "selected_item_refs": ["shown", "unseen"],
        "reason": "more alternatives",
    }
    monkeypatch.setattr(llm_module.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", FakeAsyncClient)
    service = LlmService()
    previous_items = [
        {
            "item_id": item_id,
            "source": "musinsa",
            "name": item_id,
            "image_url": f"https://image.example/{item_id}.jpg",
            "product_url": f"https://www.musinsa.com/products/{item_id}",
        }
        for item_id in ("shown", "unseen")
    ]

    plan = asyncio.run(
        service.plan_retrieval(
            query="비슷한 상품 하나 더",
            original_query="하나 더 보여줘",
            previous_rag_results=previous_items,
            previous_shown_item_refs=["shown"],
            previous_retrieval_target="musinsa",
        )
    )

    assert plan["action"] == "reuse"
    assert plan["selected_item_refs"] == ["unseen"]
    planner_prompt = json.loads(FakeAsyncClient.calls[0]["json"]["contents"][0]["parts"][0]["text"])
    assert planner_prompt["previous_rag_items"][0]["was_shown"] is True
    assert planner_prompt["previous_rag_items"][1]["was_shown"] is False
