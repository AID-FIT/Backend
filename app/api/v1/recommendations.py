from fastapi import APIRouter, Depends, HTTPException
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
    # Support both Pydantic models and plain dict-like profiles.
    if profile is None:
        return None
    return profile.model_dump() if hasattr(profile, "model_dump") else dict(profile)


@router.post("", response_model=RecommendationResponse)
async def create_recommendation(
    payload: RecommendationCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecommendationResponse:
    # 인증된 사용자 본인의 id만 신뢰한다(payload.user_id는 무시).
    user_profile = normalize_user_profile(payload.user_profile)
    result = await RecommendationService().create_and_persist(
        db=db,
        user_id=current_user.id,
        query=payload.query,
        image_urls=payload.image_urls,
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
    # Home cards reuse closet metadata and preference data as agent context.
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
async def get_recommendation(
    recommendation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecommendationResponse:
    result = await RecommendationService().get_by_id(db, recommendation_id, current_user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return RecommendationResponse(**result)
