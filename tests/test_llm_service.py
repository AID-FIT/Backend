import asyncio
import json

from app.services import llm_service as llm_module
from app.services.llm_service import LlmService


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

    service = LlmService(use_mock_ai=False)
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

    service = LlmService(use_mock_ai=False)
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
