from fastapi import APIRouter

from app.schemas.recommendation import RecommendationCreateRequest, RecommendationResponse
from app.services.recommendation_service import RecommendationService

router = APIRouter()


@router.post("", response_model=RecommendationResponse)
async def create_recommendation(payload: RecommendationCreateRequest) -> RecommendationResponse:
    result = await RecommendationService().create(
        prompt=payload.prompt,
        image_url=payload.image_url,
        user_id=payload.user_id,
        context=payload.context,
    )
    return RecommendationResponse(**result)


@router.get("/{recommendation_id}", response_model=RecommendationResponse)
async def get_recommendation(recommendation_id: str) -> RecommendationResponse:
    result = await RecommendationService().create(
        prompt="저장된 추천 조회 mock",
        image_url="mock://stored-image",
        user_id=None,
        context={"recommendation_id": recommendation_id},
    )
    result["id"] = recommendation_id
    return RecommendationResponse(**result)

