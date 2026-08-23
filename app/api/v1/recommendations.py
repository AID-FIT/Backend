from collections import Counter

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

# 스타일 키워드가 하나도 없을 때 쓰는 기본 쿼리.
_HOME_FALLBACK_QUERY = "오늘 입기 좋은 데일리 코디를 추천해줘."

_MAX_STYLE_KEYWORDS = 3


def _build_home_query(
    closet_items: list[dict],
    preferred_styles: list[str],
    age_range: str | None = None,
    prompt: str = "",
) -> str:
    """옷장 메타데이터와 사용자 선호를 조합해 벡터 검색에 유효한 쿼리를 만든다.

    지시문이 아닌 스타일 키워드 중심으로 작성하여 pgvector 임베딩이
    실제 의류 스타일과 유사도를 잡을 수 있게 한다.
    """
    parts: list[str] = []

    # 1) 연령대
    if age_range:
        parts.append(f"{age_range}")

    # 2) 선호 스타일
    if preferred_styles:
        parts.append(" ".join(preferred_styles[:_MAX_STYLE_KEYWORDS]) + " 스타일")

    # 3) 옷장에서 주요 색상/무드/카테고리 추출
    colors: Counter[str] = Counter()
    moods: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    seasons: Counter[str] = Counter()

    for item in closet_items:
        color = str(item.get("color") or "").strip()
        mood = str(item.get("mood") or "").strip()
        category = str(item.get("category") or "").strip()
        season = str(item.get("sense_of_season") or "").strip()
        if color:
            colors[color] += 1
        if mood:
            moods[mood] += 1
        if category:
            categories[category] += 1
        if season:
            seasons[season] += 1

    top_colors = [c for c, _ in colors.most_common(2)]
    top_moods = [m for m, _ in moods.most_common(2)]
    top_categories = [c for c, _ in categories.most_common(3)]
    top_seasons = [s for s, _ in seasons.most_common(1)]

    if top_colors:
        parts.append("주요 색상: " + ", ".join(top_colors))
    if top_moods:
        parts.append("무드: " + ", ".join(top_moods))
    if top_categories:
        parts.append("보유 아이템: " + ", ".join(top_categories))
    if top_seasons:
        parts.append("시즌: " + ", ".join(top_seasons))

    # 4) 추천 지시
    if parts:
        query = ". ".join(parts) + ". 이 취향에 어울리는 오늘의 코디를 추천해줘."
    else:
        query = _HOME_FALLBACK_QUERY

    # 5) 추가 요구사항
    if prompt:
        query += f" 추가 요구사항: {prompt}"

    return query


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
    closet_service = ClosetService()
    saved_closet_items = await closet_service.list_for_user(db, current_user)
    requested_ids = {item.closet_item_id for item in payload.closet_items}
    selected_closet_items = (
        [item for item in saved_closet_items if item.id in requested_ids]
        if requested_ids
        else saved_closet_items
    )
    closet_payload = [closet_service.to_agent_payload(item) for item in selected_closet_items]
    result = await RecommendationService().create_and_persist(
        db=db,
        user_id=current_user.id,
        query=payload.query,
        image_urls=payload.image_urls,
        closet_items=closet_payload,
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
    closet_service = ClosetService()
    closet_items = await closet_service.list_for_user(db, current_user)
    sizes = preference.sizes if preference else {}
    age_range = sizes.get("age_range") if isinstance(sizes, dict) else None
    closet_payload = [closet_service.to_agent_payload(item) for item in closet_items]
    user_profile = {
        "age_group": age_range,
        "preferred_styles": preference.styles if preference else [],
    }

    query = _build_home_query(
        closet_items=closet_payload,
        preferred_styles=preference.styles if preference else [],
        age_range=age_range,
        prompt=prompt,
    )

    result = await RecommendationService().create(
        query=query,
        user_id=current_user.id,
        context={
            "refresh_seed": max(refresh_seed, 0),
            "limit": 5,
            "age_range": age_range,
            "preferred_style": preference.styles if preference else [],
            "closet_items": closet_payload,
        },
        # Closet rows already contain VLM metadata; treating every owned image as
        # a newly attached reference would exclude the whole closet from retrieval.
        image_urls=[],
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
