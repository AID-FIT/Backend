from fastapi import APIRouter

from app.schemas.recommendation import RecommendationCreateRequest, RecommendationResponse
from app.services.recommendation_service import RecommendationService

router = APIRouter()


@router.post("", response_model=RecommendationResponse)
async def create_recommendation(payload: RecommendationCreateRequest) -> RecommendationResponse:
    result = await RecommendationService().create(
        query=payload.query,
        image_url=payload.image_url,
        user_id=payload.user_id,
        closet_item_id=payload.closet_item_id,
        recommendation_target=payload.recommendation_target,
        context=payload.context,
    )
    return RecommendationResponse(**result)


@router.get("/{recommendation_id}", response_model=RecommendationResponse)
async def get_recommendation(recommendation_id: str) -> RecommendationResponse:
    result = await RecommendationService().create(
        query="저장된 추천 조회 mock",
        image_url="mock://stored-image",
        user_id=None,
        closet_item_id=None,
        recommendation_target="musinsa",
        context={"recommendation_id": recommendation_id},
    )
    result["request_id"] = recommendation_id
    return RecommendationResponse(**result)
