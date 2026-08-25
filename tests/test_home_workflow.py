import asyncio
import json

from app.agent.home_workflow import HomeRecommendationWorkflow
from app.agent.nodes import AgentNodes
from app.services import llm_service as llm_module
from app.services import recommendation_service as recommendation_module
from app.services.llm_service import LlmService
from tests.test_agent_pipeline import (
    EmptyThenResultRagService,
    FakeLlmService,
    FakeRagService,
    FakeVlmService,
    fake_item,
)


QUERY = "20대 스트릿 스타일. 오늘 입기 좋은 무신사 상품을 추천해줘."


def home_kwargs(**overrides) -> dict:
    kwargs = {
        "query": QUERY,
        "user_id": "user_001",
        "closet_items": [],
        "user_profile": {"preferred_styles": ["street"]},
        "context": {"limit": 20},
        "diversify_by_category": True,
        "max_recommendations": 8,
    }
    kwargs.update(overrides)
    return kwargs


def build_workflow(
    rag_service=None,
    llm_service=None,
    vlm_service=None,
) -> tuple[HomeRecommendationWorkflow, object, object, object]:
    rag = rag_service or FakeRagService()
    llm = llm_service or FakeLlmService()
    vlm = vlm_service or FakeVlmService()
    workflow = HomeRecommendationWorkflow(AgentNodes(vlm, rag, llm))
    return workflow, vlm, rag, llm


def run(workflow: HomeRecommendationWorkflow, **overrides) -> dict:
    return asyncio.run(
        workflow.run(**home_kwargs(**overrides), return_trace=True)
    )


def collect(workflow: HomeRecommendationWorkflow, **overrides) -> list[dict]:
    async def drain() -> list[dict]:
        return [event async for event in workflow.stream(**home_kwargs(**overrides))]

    return asyncio.run(drain())


def test_home_skips_agent_planning_and_vlm_calls() -> None:
    workflow, vlm, rag, llm = build_workflow()

    trace = run(workflow)

    assert vlm.calls == []
    assert llm.intent_calls == []
    assert llm.refine_calls == []
    assert llm.plan_calls == []
    assert len(llm.calls) == 1
    assert len(rag.calls) == 1
    assert rag.calls[0]["retrieval_target"] == "musinsa"
    assert rag.calls[0]["vlm_items"] == []
    assert trace["intent"] == "fashion_service"
    assert trace["retrieval_target"] == "musinsa"
    assert trace["retrieval_action"] == "retrieve"
    assert trace["candidate_scope"] == "all"
    assert trace["rag_reused"] is False
    assert trace["resolved_query"] == QUERY


def test_home_service_does_not_compile_the_common_agent_graph(monkeypatch) -> None:
    workflow, _vlm, _rag, _llm = build_workflow()

    class ExplodingAgentPipeline:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("home must not initialize the common Agent graph")

    monkeypatch.setattr(
        recommendation_module, "AidFitAgentPipeline", ExplodingAgentPipeline
    )
    service = recommendation_module.RecommendationService(home_workflow=workflow)

    response = asyncio.run(service.create_home(**home_kwargs()))

    assert response["status"] == "success"


def test_home_ranks_and_diversifies_rag_candidates_before_the_final_llm() -> None:
    rag = FakeRagService(
        items=[
            fake_item("outer_1", final_score=0.99, category="아우터"),
            fake_item("outer_2", final_score=0.98, category="아우터"),
            fake_item("top_1", final_score=0.80, category="상의"),
            fake_item("pants_1", final_score=0.70, category="바지"),
        ]
    )
    workflow, _vlm, _rag, llm = build_workflow(rag_service=rag)

    run(workflow)

    assert [item["category"] for item in llm.calls[0]["ranked_items"]] == [
        "아우터",
        "상의",
        "바지",
        "아우터",
    ]


class AlwaysEmptyRagService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def search_request(self, request: dict) -> dict:
        self.calls.append(request)
        return {"items": [], "message": "검색 결과가 없습니다."}


def test_home_runs_exactly_one_fallback_and_returns_empty() -> None:
    rag = AlwaysEmptyRagService()
    workflow, _vlm, _rag, llm = build_workflow(rag_service=rag)

    trace = run(workflow)

    assert len(rag.calls) == 2
    assert trace["response"]["status"] == "empty"
    assert trace["response"]["recommendations"] == []
    # An empty candidate pool never needs a generative call.
    assert llm.calls == []


def test_home_fallback_results_are_ranked_and_sent_to_the_llm() -> None:
    rag = EmptyThenResultRagService()
    workflow, _vlm, _rag, llm = build_workflow(rag_service=rag)

    trace = run(workflow)

    assert len(rag.calls) == 2
    assert llm.calls[0]["ranked_items"][0]["item_id"] == "fallback_item"
    assert trace["response"]["status"] == "success"


def test_home_stream_reports_only_executed_home_stages() -> None:
    workflow, _vlm, _rag, _llm = build_workflow()

    events = collect(workflow)
    nodes = [event["node"] for event in events if event["type"] == "step"]

    assert nodes == ["musinsa_rag", "style_ranker", "final_response"]
    assert set(nodes).isdisjoint(
        {"intent_classifier", "query_refiner", "retrieval_planner", "vlm"}
    )
    assert events[-1]["type"] == "result"


def test_home_stream_reports_the_single_fallback_when_used() -> None:
    workflow, _vlm, _rag, _llm = build_workflow(
        rag_service=EmptyThenResultRagService()
    )

    nodes = [
        event["node"]
        for event in collect(workflow)
        if event["type"] == "step"
    ]

    assert nodes == [
        "musinsa_rag",
        "fallback_search",
        "style_ranker",
        "final_response",
    ]


class FakeGeminiResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(self.payload)}]}}
            ]
        }


class FakeGeminiClient:
    calls: list[dict] = []
    response_payload: dict = {}

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, headers: dict, json: dict) -> FakeGeminiResponse:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return FakeGeminiResponse(self.response_payload)


def test_home_final_llm_can_only_return_actual_ranked_candidates(monkeypatch) -> None:
    known = fake_item("known", final_score=0.9, category="바지")
    FakeGeminiClient.calls = []
    FakeGeminiClient.response_payload = {
        "status": "success",
        "message": "추천 결과입니다.",
        "recommendations": [
            {
                "item_id": "invented",
                "source": "musinsa",
                "image_url": "https://invented.example/item.jpg",
                "reason": "존재하지 않는 상품",
            },
            {
                "item_id": "known",
                "source": "musinsa",
                "image_url": "https://wrong.example/item.jpg",
                "reason": "실제 후보 중 선택",
            },
        ],
        "style_guide": {"summary": "스트릿 코디", "tips": []},
    }
    monkeypatch.setattr(llm_module.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", FakeGeminiClient)
    workflow, _vlm, _rag, _llm = build_workflow(
        rag_service=FakeRagService(items=[known]),
        llm_service=LlmService(),
    )

    trace = run(workflow)

    recommendations = trace["response"]["recommendations"]
    assert len(FakeGeminiClient.calls) == 1
    assert [item["item_id"] for item in recommendations] == ["known"]
    assert recommendations[0]["image_url"] == known["image_url"]
    assert recommendations[0]["product_url"] == known["product_url"]


class ExplodingRagService:
    async def search_request(self, _request: dict) -> dict:
        raise RuntimeError("secret vector database credential")


class ExplodingLlmService(FakeLlmService):
    async def compose_recommendation(self, *args, **kwargs) -> dict:
        raise RuntimeError("secret GEMINI_API_KEY")


def test_home_rag_failure_uses_the_public_error_contract() -> None:
    workflow, _vlm, _rag, _llm = build_workflow(
        rag_service=ExplodingRagService()
    )

    response = run(workflow)["response"]

    assert response["status"] == "error"
    assert response["recommendations"] == []
    assert "secret" not in response["message"].lower()


def test_home_llm_failure_uses_the_public_error_contract() -> None:
    workflow, _vlm, _rag, _llm = build_workflow(
        llm_service=ExplodingLlmService()
    )

    response = run(workflow)["response"]

    assert response["status"] == "error"
    assert response["recommendations"] == []
    assert "gemini" not in response["message"].lower()
