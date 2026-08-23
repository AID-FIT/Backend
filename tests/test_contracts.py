import pytest
from pydantic import ValidationError

from app.schemas.recommendation import AgentRequest, AgentResponse, RecommendationItem, RecommendationResponse


def test_recommendation_response_contract() -> None:
    response = RecommendationResponse(
        status="success",
        message="테스트 추천",
        recommendations=[],
    )

    assert response.model_dump()["status"] == "success"


def test_agent_response_contract_full_payload() -> None:
    response = AgentResponse(
        status="success",
        message="화이트 셔츠에는 미니멀한 세미 와이드 데님 팬츠가 잘 어울립니다.",
        recommendations=[
            {
                "item_id": "musinsa_10001",
                "source": "musinsa",
                "item_name": "세미 와이드 데님 팬츠",
                "brand": "Example Brand",
                "category": "pants",
                "image_url": "https://image.musinsa.com/10001.jpg",
                "product_url": "https://www.musinsa.com/products/10001",
                "price": 59000,
                "reason": "화이트 셔츠의 깔끔한 무드와 데님의 캐주얼함이 잘 어울립니다.",
            }
        ],
        style_guide={
            "summary": "미니멀 캐주얼 코디",
            "tips": [
                "상의가 밝은 색이므로 하의는 중청 또는 진청 계열이 안정적입니다.",
                "신발은 화이트 스니커즈를 추천합니다.",
            ],
        },
    )

    assert set(response.model_dump().keys()) == {"status", "message", "recommendations", "style_guide"}
    assert response.recommendations[0].product_url == "https://www.musinsa.com/products/10001"


def test_agent_response_contract_empty_payload() -> None:
    response = AgentResponse(
        status="empty",
        message="조건에 맞는 추천 상품을 찾지 못했습니다.",
        recommendations=[],
        style_guide=None,
    )

    assert response.model_dump() == {
        "status": "empty",
        "message": "조건에 맞는 추천 상품을 찾지 못했습니다.",
        "recommendations": [],
        "style_guide": None,
    }


def test_musinsa_recommendation_requires_product_url() -> None:
    with pytest.raises(ValidationError):
        RecommendationItem(
            source="musinsa",
            image_url="https://image.musinsa.com/10001.jpg",
            reason="상품 URL이 없으면 musinsa 추천으로 반환하지 않습니다.",
        )


def test_agent_request_contract_defaults() -> None:
    request = AgentRequest(user_id="user_001", query="화이트 니트랑 어울리는 바지 추천해줘")

    assert request.image_urls == []
    assert request.closet_items == []
    assert request.use_closet_style is True
    assert request.user_profile is None


def test_agent_request_contract_full_payload() -> None:
    request = AgentRequest(
        user_id="user_001",
        query="화이트 니트랑 어울리는 바지 추천해줘",
        image_urls=[],
        closet_items=[
            {
                "closet_item_id": "closet_001",
                "name": "에이프 헤드 클리어 백",
                "brand": "베이프",
                "price": 115000,
                "category": "가방",
                "label": "남자 가방",
                "gender": "men",
                "image_url": "https://cdn.aidfit.com/closet_001.jpg",
                "product_url": None,
                "color": "black",
                "material": "pvc",
                "fit": "none",
                "pattern": "graphic",
                "mood": "street",
                "sense_of_season": "summer",
            }
        ],
        use_closet_style=True,
        user_profile={
            "age_group": "20s",
            "preferred_styles": ["minimal", "casual"],
        },
    )

    assert request.closet_items[0].closet_item_id == "closet_001"
    assert request.closet_items[0].image_url == "https://cdn.aidfit.com/closet_001.jpg"
    assert request.closet_items[0].sense_of_season == "summer"
    assert request.user_profile is not None
    assert request.user_profile.preferred_styles == ["minimal", "casual"]


def test_agent_request_requires_query() -> None:
    with pytest.raises(ValidationError):
        AgentRequest(user_id="user_001")
