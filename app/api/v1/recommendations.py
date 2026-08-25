import json
import logging
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.recommendation import RecommendationCreateRequest, RecommendationResponse
from app.services.closet_service import ClosetService
from app.services.recommendation_service import RecommendationService
from app.services.target_category import infer_target_category
from app.services.user_service import UserService, to_agent_profile

router = APIRouter()
logger = logging.getLogger(__name__)

# LLM이 이유까지 써 주는 큐레이션 타일 수. 피드 맨 앞에 놓인다.
_HOME_CURATED_COUNT = 8
# 홈 피드가 싣는 전체 타일 수. 카테고리 칩을 클라이언트에서 거르는 구조라
# 카테고리(7종)마다 두 줄 이상은 남아 있어야 칩이 빈 화면을 내지 않는다.
# 프론트가 2열 그리드라 짝수여야 마지막 줄이 비지 않는다.
_HOME_FEED_SIZE = 36
# 후보 풀은 피드를 채우고도 남아야 한다. LLM 호출이 아니라 pgvector 검색이라
# 늘려도 비용이 거의 들지 않는다. 새로고침이 겹치지 않은 상품을 보여주려면
# 훑는 후보 수(candidate_limit_for)가 이 값의 배수여야 한다.
_HOME_CANDIDATE_POOL = 100
# 스타일 키워드가 하나도 없을 때 쓰는 기본 쿼리.
_HOME_FALLBACK_QUERY = "오늘 입기 좋은 데일리 코디를 추천해줘."

_MAX_STYLE_KEYWORDS = 3

# 홈 필터에서 받아 주는 값. 카탈로그(product_vectors)에 실제로 들어 있는 값과
# 같아야 하므로 여기서 못박는다. 모르는 값은 400을 내지 않고 무시한다 —
# 필터 하나 때문에 홈이 통째로 비는 것보다 낫다.
_HOME_CATEGORIES = frozenset(
    {"상의", "바지", "아우터", "신발", "가방", "모자", "원피스/스커트"}
)
_HOME_MOODS = frozenset(
    {"casual", "street", "minimal", "sporty", "classic", "feminine", "cute", "vintage", "outdoor"}
)
_HOME_SEASONS = frozenset({"spring", "summer", "fall", "winter"})


def _allowed(value: str, allowed: frozenset[str]) -> str | None:
    normalized = (value or "").strip()
    if normalized in allowed:
        return normalized
    # 무드·계절은 소문자 영문이라 대소문자만 다른 입력도 받아 준다.
    lowered = normalized.lower()
    return lowered if lowered in allowed else None


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
    seasons: Counter[str] = Counter()

    for item in closet_items:
        color = str(item.get("color") or "").strip()
        mood = str(item.get("mood") or "").strip()
        season = str(item.get("sense_of_season") or "").strip()
        if color:
            colors[color] += 1
        if mood:
            moods[mood] += 1
        if season:
            seasons[season] += 1

    top_colors = [c for c, _ in colors.most_common(2)]
    top_moods = [m for m, _ in moods.most_common(2)]
    top_seasons = [s for s, _ in seasons.most_common(1)]

    if top_colors:
        parts.append("주요 색상: " + ", ".join(top_colors))
    if top_moods:
        parts.append("무드: " + ", ".join(top_moods))
    # 보유 카테고리는 쿼리에 넣지 않는다. "보유 아이템: 상의, 바지"가 찾는 옷으로
    # 오독돼 검색이 한 카테고리로 좁혀진다. 옷장 정보는 closet_items로 이미 전달된다.
    if top_seasons:
        parts.append("시즌: " + ", ".join(top_seasons))

    taste = ". ".join(parts)
    request = prompt.strip()

    # 4) 추천 지시 — "무신사"를 명시해 retrieval planner가 catalog 검색을 택하게 한다.
    if not request:
        return f"{taste}. 이 취향에 어울리는 무신사 상품으로 오늘의 코디를 추천해줘." if taste else _HOME_FALLBACK_QUERY

    # 검색어는 사용자가 방금 입력한 가장 강한 신호다. 취향 문장 뒤에
    # "추가 요구사항: 바지"로 덧붙이면 두 가지가 깨진다.
    #   - infer_target_category가 "추천" 앞만 보므로 "바지"를 목표로 읽지 못한다.
    #   - 긴 취향 문장에 묻혀 임베딩에서 비중을 잃는다.
    # 그래서 문장 앞에 세우고, "추천"보다 앞에 오게 한다.
    return (
        f"{request}. {taste}. 이 조건에 맞는 무신사 상품을 추천해줘."
        if taste
        else f"{request}. 이 조건에 맞는 무신사 상품을 추천해줘."
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


async def _build_home_request(
    db: AsyncSession,
    current_user: User,
    prompt: str,
    refresh_seed: int,
    category: str,
    mood: str,
    season: str,
) -> dict:
    """홈 추천 한 건에 필요한 인자를 모은다.

    `/home`과 `/home/stream`이 같은 조건으로 돌아야 한다. 한쪽에만 필터를
    추가하면 스트리밍과 폴백이 서로 다른 결과를 주게 된다.
    """
    user_service = UserService()
    preference = await user_service.get_preference(db, current_user)
    closet_service = ClosetService()
    closet_items = await closet_service.list_for_user(db, current_user)
    closet_payload = [closet_service.to_agent_payload(item) for item in closet_items]
    user_profile = to_agent_profile(preference)
    age_range = user_profile["age_group"]
    preferred_styles = user_profile["preferred_styles"]
    gender = user_profile["gender"]

    query = _build_home_query(
        closet_items=closet_payload,
        preferred_styles=preferred_styles,
        age_range=age_range,
        prompt=prompt,
    )

    # 칩으로 고른 카테고리가 질의에서 추론한 값보다 우선한다. 사용자가 직접
    # 누른 것이므로 추측이 이길 이유가 없다.
    selected_category = _allowed(category, _HOME_CATEGORIES)
    target_category = selected_category or infer_target_category(query)

    context: dict = {
        "refresh_seed": max(refresh_seed, 0),
        # 후보를 뽑을 개수만큼만 뽑으면 LLM이 그중 마음에 드는 것만 골라
        # 1~2개로 줄어든다. 피드를 채울 몫까지 넉넉히 뽑는다.
        "limit": _HOME_CANDIDATE_POOL,
        "age_range": age_range,
        "preferred_style": preferred_styles,
        "closet_items": closet_payload,
    }
    # unisex는 "가리지 않는다"는 뜻이므로 조건으로 걸지 않는다.
    if gender and gender != "unisex":
        context["gender"] = gender
    if selected_category:
        context["category"] = selected_category
    selected_mood = _allowed(mood, _HOME_MOODS)
    if selected_mood:
        context["mood"] = selected_mood
    selected_season = _allowed(season, _HOME_SEASONS)
    if selected_season:
        context["season"] = selected_season

    return {
        "applied_filters": {
            "category": selected_category,
            "mood": selected_mood,
            "season": selected_season,
            "age_range": age_range,
            "gender": gender,
            "preferred_styles": list(preferred_styles),
            "prompt": prompt.strip(),
        },
        "run_kwargs": {
            "query": query,
            "user_id": current_user.id,
            "context": context,
            # Closet rows already contain VLM metadata; treating every owned image as
            # a newly attached reference would exclude the whole closet from retrieval.
            "image_urls": [],
            "closet_items": closet_payload,
            "use_closet_style": True,
            "user_profile": user_profile,
            # 홈 타일은 "사러 갈 만한 상품"을 보여주는 자리다. 검색 계획이 closet이나
            # hybrid를 고르면 사용자가 이미 가진 옷이 타일로 올라온다. 그걸 막는다.
            "recommendation_target": "musinsa",
            "lock_retrieval_target": True,
            # 겨울 검정 스트릿처럼 한 쪽으로 쏠린 취향이면 상위 후보가 전부 아우터가
            # 된다. 타일이 같은 종류로만 차는 것을 막는다. 다만 "바지"처럼 종류를 찍어
            # 검색했다면 섞는 쪽이 오히려 틀린 답이므로 그때는 끈다.
            "diversify_by_category": target_category is None,
            "max_recommendations": _HOME_CURATED_COUNT,
        },
    }


def _tile_ref(item: dict) -> str:
    """같은 상품인지 가리는 키.

    카탈로그 행에 item_id가 비어 오는 경우가 있어 이미지 주소도 함께 본다.
    """
    item_id = item.get("item_id")
    if item_id is not None and str(item_id).strip():
        return f"id:{item_id}"
    return f"url:{item.get('image_url') or ''}"


def _as_feed_tile(item: dict) -> dict | None:
    """랭킹 결과를 홈 타일 한 칸으로 바꾼다. 타일이 될 수 없으면 None."""
    image_url = item.get("image_url")
    source = item.get("source", "musinsa")
    product_url = item.get("product_url")
    # RecommendationItem은 무신사 상품에 product_url을 요구한다. 하나라도
    # 비어 있으면 응답 전체가 검증에서 떨어지므로 여기서 걸러 낸다.
    if not image_url or (source == "musinsa" and not product_url):
        return None
    return {
        "item_id": item.get("item_id"),
        "source": source,
        "item_name": item.get("item_name") or item.get("name"),
        "brand": item.get("brand"),
        "category": item.get("category"),
        "image_url": image_url,
        "product_url": product_url,
        "price": item.get("price"),
        # 이유는 LLM이 고른 앞쪽 타일에만 붙는다. 나머지는 검색·랭킹이 그대로
        # 실은 것이라 지어낸 이유를 달지 않는다. 프론트는 빈 이유를 그리지 않는다.
        "reason": "",
    }


def _fill_home_feed(response: dict, ranked_items: list[dict]) -> dict:
    """LLM이 고른 타일 뒤에 랭킹 상위 상품을 카테고리 순환으로 붙인다.

    칩을 클라이언트에서 거르는 구조라 카테고리마다 타일이 여러 칸 남아
    있어야 한다. 그렇다고 LLM에 36개를 쓰게 하면 프롬프트도 생성도 네 배로
    불어난다. 이유가 필요한 앞쪽만 LLM이 쓰고, 나머지는 이미 뽑아 둔 검색
    결과를 그대로 싣는다. LLM 호출은 늘지 않는다.
    """
    recommendations = list(response.get("recommendations") or [])
    if response.get("status") != "success" or len(recommendations) >= _HOME_FEED_SIZE:
        return response

    seen = {_tile_ref(item) for item in recommendations}
    buckets: dict[str, list[dict]] = {}
    for item in ranked_items:
        tile = _as_feed_tile(item)
        if tile is None or _tile_ref(tile) in seen:
            continue
        seen.add(_tile_ref(tile))
        buckets.setdefault(tile.get("category") or "기타", []).append(tile)

    # 카테고리를 번갈아 채운다. 점수순으로만 자르면 상위가 한 종류로 쏠려
    # 다른 칩을 눌렀을 때 결과가 비어 버린다.
    while buckets and len(recommendations) < _HOME_FEED_SIZE:
        for category in list(buckets):
            if len(recommendations) >= _HOME_FEED_SIZE:
                break
            recommendations.append(buckets[category].pop(0))
            if not buckets[category]:
                del buckets[category]

    return {**response, "recommendations": recommendations}


@router.get("/home", response_model=RecommendationResponse)
async def get_home_recommendation(
    prompt: str = "",
    refresh_seed: int = 0,
    category: str = "",
    mood: str = "",
    season: str = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecommendationResponse:
    request = await _build_home_request(
        db, current_user, prompt, refresh_seed, category, mood, season
    )
    # 피드를 채우려면 LLM이 고르고 남은 랭킹 결과가 필요하다. 트레이스로 받는다.
    trace = await RecommendationService().create(**request["run_kwargs"], return_trace=True)
    result = _fill_home_feed(trace["response"], trace.get("ranked_items") or [])
    return RecommendationResponse(
        **result,
        applied_filters={
            **request["applied_filters"],
            "result_count": len(result.get("recommendations") or []),
        },
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/home/stream")
async def stream_home_recommendation(
    prompt: str = "",
    refresh_seed: int = 0,
    category: str = "",
    mood: str = "",
    season: str = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """홈 추천을 만들면서 진행 상황을 흘린다.

    `/home`과 같은 조건으로 돌고 결과도 같다. 다른 점은 13초를 기다리는 동안
    어느 단계인지 보인다는 것뿐이다. 스트리밍이 막힌 환경을 위해 `/home`은
    그대로 남겨 둔다.
    """
    request = await _build_home_request(
        db, current_user, prompt, refresh_seed, category, mood, season
    )

    async def events():
        service = RecommendationService()
        try:
            async for event in service.stream(**request["run_kwargs"]):
                if event["type"] != "result":
                    yield _sse(event)
                    continue

                response = _fill_home_feed(
                    event["response"], event.get("ranked_items") or []
                )
                yield _sse(
                    {
                        "type": "result",
                        **response,
                        "applied_filters": {
                            **request["applied_filters"],
                            "result_count": len(response.get("recommendations") or []),
                        },
                    }
                )
        except Exception:
            # 헤더는 이미 나갔으므로 HTTP 상태로는 실패를 알릴 수 없다.
            # 스트림 안에서 알려 주지 않으면 화면이 영원히 로딩에 머문다.
            logger.exception("home recommendation stream failed")
            yield _sse(
                {
                    "type": "error",
                    "message": "추천을 만드는 중에 문제가 생겼어요. 다시 시도해 주세요.",
                }
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 프록시가 응답을 통째로 모았다가 보내면 스트리밍이 의미를 잃는다.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


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
