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


def normalize_user_profile(profile: object | None) -> dict | None:
    if profile is None:
        return None
    return profile.model_dump() if hasattr(profile, "model_dump") else dict(profile)


@router.post("", response_model=RecommendationResponse)
async def create_recommendation(payload: RecommendationCreateRequest) -> RecommendationResponse:
    image_urls = payload.image_urls
    user_profile = normalize_user_profile(payload.user_profile)
    result = await RecommendationService().create(
        query=payload.query,
        user_id=payload.user_id,
        image_urls=image_urls,
        closet_items=[item.model_dump() for item in payload.closet_items],
        use_closet_style=payload.use_closet_style,
        user_profile=user_profile,
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
    closet_payload = [
        {
            "closet_item_id": item.id,
            "category": item.category,
            "color": item.color,
            "material": item.material,
            "fit": item.fit,
            "pattern": item.pattern,
            "mood": item.mood,
            "sense_of_season": item.sense_of_season,
        }
        for item in closet_items
    ]
    user_profile = {
        "age_group": age_range,
        "preferred_styles": preference.styles if preference else [],
    }

    result = await RecommendationService().create(
        query=HOME_RECOMMENDATION_QUERY.format(prompt=prompt or "없음"),
        user_id=current_user.id,
        context={
            "refresh_seed": max(refresh_seed, 0),
            "limit": 5,
            "outfit_set": True,
            "age_range": age_range,
            "preferred_style": preference.styles if preference else [],
            "closet_items": closet_payload,
        },
        image_urls=[item.image_url for item in closet_items if item.image_url],
        closet_items=closet_payload,
        use_closet_style=True,
        user_profile=user_profile,
    )
    return RecommendationResponse(**result)


@router.get("/{recommendation_id}", response_model=RecommendationResponse)
async def get_recommendation(recommendation_id: str) -> RecommendationResponse:
    result = await RecommendationService().create(
        query="저장된 추천 조회 mock",
        user_id="mock_user",
        context={"recommendation_id": recommendation_id},
        image_urls=["mock://stored-image"],
        closet_items=[],
        use_closet_style=True,
        user_profile=None,
    )
    return RecommendationResponse(**result)
