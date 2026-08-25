import pytest
from pydantic import ValidationError

from app.schemas.ai import (
    IntentClassification,
    QueryRefinement,
    RAGItem,
    RAGRequest,
    RAGResponse,
    RetrievalPlan,
    VLMResponse,
)
from app.schemas.recommendation import AgentResponse


def test_vlm_response_contract_accepts_valid_payload() -> None:
    response = VLMResponse(
        items=[
            {
                "category": "top",
                "color": "white",
                "material": "knit",
                "fit": "oversized",
                "mood": "minimal",
                "sense_of_season": "spring",
            }
        ],
        is_fashion_item=True,
    )

    assert response.items[0].color == "white"


def test_vlm_response_contract_rejects_unknown_fields() -> None:
    # Extra fields should fail loudly when service contracts drift.
    with pytest.raises(ValidationError):
        VLMResponse(items=[{"category": "top", "unknown": "field"}])


def test_rag_request_contract_contains_agent_context() -> None:
    request = RAGRequest(
        user_id="user_001",
        query="white knit pants",
        retrieval_target="hybrid",
        user_profile={"age_group": "20s"},
        vlm_items=[{"category": "top", "color": "white"}],
        closet_items=[{"closet_item_id": "closet_001"}],
        use_closet_style=True,
        filters={"season": "spring"},
        top_k=10,
    )

    assert request.vlm_items[0]["color"] == "white"
    assert request.closet_items[0]["closet_item_id"] == "closet_001"
    assert request.retrieval_target == "hybrid"


def test_rag_request_defaults_to_thirty_candidates() -> None:
    request = RAGRequest(
        user_id="user_001",
        query="가을 옷 추천해줘",
        retrieval_target="musinsa",
    )

    assert request.top_k == 30


def test_rag_item_requires_musinsa_product_url() -> None:
    # Musinsa recommendations need product URLs for client navigation.
    with pytest.raises(ValidationError):
        RAGItem(source="musinsa", image_url="https://image.example/item.jpg")


def test_rag_response_contract_accepts_valid_musinsa_item() -> None:
    response = RAGResponse(
        items=[
            {
                "source": "musinsa",
                "image_url": "https://image.example/item.jpg",
                "product_url": "https://www.musinsa.com/products/10001",
            }
        ],
        message="success",
    )

    assert response.items[0].product_url == "https://www.musinsa.com/products/10001"


def test_agent_response_contract_validates_llm_output() -> None:
    response = AgentResponse(
        status="success",
        message="추천 결과입니다.",
        recommendations=[
            {
                "source": "musinsa",
                "image_url": "https://image.example/item.jpg",
                "product_url": "https://www.musinsa.com/products/10001",
                "reason": "화이트 니트와 잘 어울립니다.",
            }
        ],
        style_guide={"summary": "미니멀 캐주얼", "tips": []},
    )

    assert response.status == "success"


def test_llm_routing_contracts_accept_structured_outputs() -> None:
    intent = IntentClassification(intent="fashion_service", reason="styling request")
    refinement = QueryRefinement(
        query="화이트 니트에 어울리는 봄 팬츠",
        request_mode="coordination",
        target_category="바지",
    )
    plan = RetrievalPlan(
        action="reuse",
        retrieval_target="musinsa",
        candidate_scope="unseen",
        selected_item_refs=["item_001"],
    )

    assert intent.intent == "fashion_service"
    assert refinement.query.endswith("봄 팬츠")
    assert refinement.request_mode == "coordination"
    assert refinement.target_category == "바지"
    assert plan.candidate_scope == "unseen"
    assert plan.selected_item_refs == ["item_001"]


def test_query_refinement_contract_has_no_filter_output() -> None:
    assert set(QueryRefinement.model_fields) == {
        "query",
        "request_mode",
        "target_category",
    }


def test_llm_routing_contracts_reject_unknown_decisions() -> None:
    with pytest.raises(ValidationError):
        IntentClassification(intent="rule_based_route")

    with pytest.raises(ValidationError):
        RetrievalPlan(action="skip", retrieval_target="musinsa")
