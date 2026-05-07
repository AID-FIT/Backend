from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.recommendation import RecommendationCreateRequest, RecommendationResponse
from app.services.closet_service import ClosetService
from app.services.recommendation_service import RecommendationService
from app.services.user_service import UserService

router = APIRouter()

HOME_RECOMMENDATION_QUERY = (
    "사용자의 옷장과 취향을 기반으로 하고, 이미지가 존재한다면, 이미지와 매칭이 되는 오늘 입기 좋은 "
    "코디를 추천해주고, 이미지가 존재하지 않다면 사용자의 옷장과 취향을 기반으로만 추천해줘. "
    "단, 사용자의 추가 요구사항이 있다면, 추가 요구사항은 최우선적으로 적용해 줘. 추가 요구사항 : {prompt}"
)


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


@router.get("/home", response_model=RecommendationResponse)
async def get_home_recommendation(
    prompt: str = "",
    refresh_seed: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecommendationResponse:
    preference = await UserService().get_preference(db, current_user)
    closet_items = await ClosetService().list_for_user(db, current_user)
    sizes = preference.sizes if preference else {}
    age_range = sizes.get("age_range") if isinstance(sizes, dict) else None

    result = await RecommendationService().create(
        query=HOME_RECOMMENDATION_QUERY.format(prompt=prompt or "없음"),
        image_url=closet_items[0].image_url if closet_items else None,
        user_id=current_user.id,
        closet_item_id=closet_items[0].id if closet_items else None,
        recommendation_target="musinsa",
        context={
            "refresh_seed": max(refresh_seed, 0),
            "limit": 5,
            "age_range": age_range,
            "preferred_style": preference.styles if preference else [],
            "closet_items": [
                {
                    "closet_item_id": item.id,
                    "category": item.category,
                    "color": item.color,
                    "material": item.material,
                    "fit": item.fit,
                    "pattern": item.pattern,
                    "mood": item.mood,
                    "sense of season": item.sense_of_season,
                }
                for item in closet_items
            ],
        },
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
