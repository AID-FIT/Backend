"""홈 진행 스트리밍 엔드포인트.

`/home`과 같은 조건으로 돌되, 13초를 기다리는 동안 어느 단계인지 보인다.
스트림은 헤더가 이미 나간 뒤에 실패할 수 있어 오류 처리 방식이 다르다.
"""

import asyncio
import json

import pytest

from app.api.v1 import recommendations as home_api


class StubPreference:
    styles = ["스트릿"]
    sizes = {"age_range": "20대"}
    gender = "men"
    height_cm = 178


class StubUserService:
    async def get_preference(self, _db, _user):
        return StubPreference()


class StubClosetService:
    async def list_for_user(self, _db, _user):
        return []

    def to_agent_payload(self, item):
        return item


class StubUser:
    id = "user_001"


RESULT_EVENT = {
    "type": "result",
    "response": {
        "status": "success",
        "message": "추천이에요",
        "recommendations": [{"item_id": "a"}, {"item_id": "b"}],
        "style_guide": None,
    },
}


class StubRecommendationService:
    """진행 이벤트 두 개와 결과 하나를 흘린다."""

    events: list[dict] = [
        {"type": "step", "node": "musinsa_rag", "label": "상품에서 골랐어요", "detail": "후보 30건"},
        {"type": "step", "node": "style_ranker", "label": "취향에 맞게 순서를 매겼어요", "detail": "3가지 종류"},
        RESULT_EVENT,
    ]

    def __init__(self) -> None:
        self.received: dict = {}

    def stream_home(self, **kwargs):
        self.received.update(kwargs)

        async def generate():
            for event in self.events:
                yield event

        return generate()


def ranked_leftovers(count: int = 60) -> list[dict]:
    """LLM이 아직 쓰지 않은 랭킹 결과. 피드의 뒷칸을 채우는 재료다."""
    return [
        {
            "item_id": f"ranked_{index}",
            "source": "musinsa",
            "item_name": f"상품 {index}",
            "brand": "brand",
            "category": ["상의", "바지", "아우터"][index % 3],
            "image_url": f"https://img/{index}.jpg",
            "product_url": f"https://shop/{index}",
            "price": 10000,
        }
        for index in range(count)
    ]


class FillingRecommendationService(StubRecommendationService):
    """큐레이션 두 칸과, 아직 쓰지 않은 랭킹 결과를 함께 흘린다."""

    events = [
        {
            **RESULT_EVENT,
            "ranked_items": ranked_leftovers(),
        },
    ]


class ExplodingRecommendationService(StubRecommendationService):
    def stream_home(self, **kwargs):
        async def generate():
            yield {"type": "step", "node": "musinsa_rag", "label": "상품을 찾고 있어요", "detail": None}
            raise RuntimeError("gemini is down")

        return generate()


@pytest.fixture(autouse=True)
def stub_dependencies(monkeypatch):
    monkeypatch.setattr(home_api, "UserService", StubUserService)
    monkeypatch.setattr(home_api, "ClosetService", StubClosetService)


def drain(service_class=StubRecommendationService, **overrides) -> list[dict]:
    monkey = service_class
    original = home_api.RecommendationService
    home_api.RecommendationService = monkey
    try:
        kwargs = {"prompt": "", "refresh_seed": 0, "category": "", "mood": "", "season": ""}
        kwargs.update(overrides)

        async def run() -> list[str]:
            response = await home_api.stream_home_recommendation(
                current_user=StubUser(), db=None, **kwargs
            )
            return [chunk async for chunk in response.body_iterator]

        chunks = asyncio.run(run())
    finally:
        home_api.RecommendationService = original

    return [json.loads(chunk.removeprefix("data: ").strip()) for chunk in chunks]


def response_headers(**overrides):
    original = home_api.RecommendationService
    home_api.RecommendationService = StubRecommendationService
    try:
        kwargs = {"prompt": "", "refresh_seed": 0, "category": "", "mood": "", "season": ""}
        kwargs.update(overrides)
        response = asyncio.run(
            home_api.stream_home_recommendation(current_user=StubUser(), db=None, **kwargs)
        )
        return response
    finally:
        home_api.RecommendationService = original


def test_stream_fills_the_feed_from_the_ranked_leftovers() -> None:
    """스트리밍도 `/home`과 같은 크기의 피드를 보내야 한다.

    한쪽만 채우면 브라우저(스트리밍)와 네이티브(폴백)가 서로 다른 개수를
    받아, 카테고리 칩이 한쪽에서만 걸린다.
    """
    result = drain(FillingRecommendationService)[-1]

    assert len(result["recommendations"]) == home_api._HOME_FEED_SIZE


def test_stream_counts_the_filled_feed_not_just_the_curated_tiles() -> None:
    result = drain(FillingRecommendationService)[-1]

    assert result["applied_filters"]["result_count"] == home_api._HOME_FEED_SIZE


def test_stream_sends_sse_events() -> None:
    events = drain()

    assert [event["type"] for event in events] == ["step", "step", "result"]


def test_stream_does_not_report_removed_agent_steps() -> None:
    nodes = {event.get("node") for event in drain() if event["type"] == "step"}

    assert nodes.isdisjoint(
        {"intent_classifier", "query_refiner", "retrieval_planner", "vlm"}
    )


def test_stream_declares_the_event_stream_media_type() -> None:
    # 이 헤더가 없으면 브라우저가 응답을 스트림으로 다루지 않는다.
    assert response_headers().media_type == "text/event-stream"


def test_stream_asks_proxies_not_to_buffer() -> None:
    # 프록시가 응답을 통째로 모았다가 보내면 단계 표시가 의미를 잃는다.
    headers = response_headers().headers

    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"


def test_result_event_carries_the_recommendations() -> None:
    result = drain()[-1]

    assert result["status"] == "success"
    assert len(result["recommendations"]) == 2


def test_result_event_reports_the_applied_filters() -> None:
    result = drain(category="바지", season="summer", prompt="청바지")[-1]
    applied = result["applied_filters"]

    assert applied["category"] == "바지"
    assert applied["season"] == "summer"
    assert applied["prompt"] == "청바지"
    assert applied["result_count"] == 2


def test_unknown_filter_values_are_ignored() -> None:
    applied = drain(category="양말")[-1]["applied_filters"]

    assert applied["category"] is None


def test_failure_arrives_as_an_event_not_a_dropped_connection() -> None:
    # 헤더가 이미 나갔으므로 HTTP 상태로는 실패를 알릴 수 없다.
    # 알리지 않으면 화면이 영원히 로딩에 머문다.
    events = drain(ExplodingRecommendationService)

    assert events[-1]["type"] == "error"
    assert events[-1]["message"]


def test_failure_message_does_not_leak_internals() -> None:
    assert "gemini" not in drain(ExplodingRecommendationService)[-1]["message"].lower()


def test_korean_survives_the_json_encoding() -> None:
    # ensure_ascii를 켜 두면 화면에 \uxxxx가 뜬다.
    assert "상품" in drain()[0]["label"]
