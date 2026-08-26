import asyncio

from app.agent.nodes import AgentNodes
from app.api.v1.recommendations import _HOME_CANDIDATE_POOL, _build_home_query
from app.services.target_category import infer_target_category

CLOSET = [
    {"category": "아우터", "color": "black", "mood": "street", "sense_of_season": "winter"},
    {"category": "바지", "color": "black", "mood": "street", "sense_of_season": "fall"},
    {"category": "상의", "color": "black", "mood": "street", "sense_of_season": "summer"},
]


def home_query(**overrides) -> str:
    kwargs = {
        "closet_items": CLOSET,
        "preferred_styles": ["스트릿", "캐주얼"],
        "age_range": "20대",
        "prompt": "",
    }
    kwargs.update(overrides)
    return _build_home_query(**kwargs)


def test_home_query_carries_closet_style_signals() -> None:
    query = home_query()

    assert "20대" in query
    assert "스트릿" in query
    assert "black" in query
    assert "street" in query


def test_home_query_does_not_list_owned_categories() -> None:
    # "보유 아이템: 상의, 바지"를 넣으면 찾는 옷으로 오독돼 검색이 한 카테고리로
    # 좁혀진다. 옷장 정보는 closet_items로 따로 전달된다.
    query = home_query()

    assert infer_target_category(query) is None


def test_home_query_falls_back_without_any_signal() -> None:
    query = home_query(closet_items=[], preferred_styles=[], age_range=None)

    assert "데일리 코디" in query
    assert infer_target_category(query) is None


def test_specific_home_query_excludes_profile_and_closet_taste() -> None:
    query = home_query(prompt="비 오는 날 입을 옷")

    assert query.startswith("비 오는 날 입을 옷")
    assert "스트릿" not in query
    assert "black" not in query


def test_vague_home_prompt_is_supplemented_with_taste() -> None:
    query = home_query(prompt="오늘 뭐 입지")

    assert "스트릿" in query
    assert "black" in query


def test_search_term_becomes_the_target_category() -> None:
    # "바지"를 검색했으면 바지가 나와야 한다. 취향 문장이 아무리 길어도 마찬가지다.
    assert infer_target_category(home_query(prompt="바지")) == "바지"


def test_search_works_without_any_taste_signal() -> None:
    query = home_query(prompt="바지", closet_items=[], preferred_styles=[], age_range=None)

    assert query.startswith("바지")
    assert infer_target_category(query) == "바지"


def test_style_keyword_search_keeps_no_target_category() -> None:
    # 칩(캐주얼·여름·미니멀·데이트룩)은 종류가 아니라 무드다. 좁히면 안 된다.
    for keyword in ("캐주얼", "여름", "미니멀", "데이트룩"):
        assert infer_target_category(home_query(prompt=keyword)) is None


def test_home_candidate_pool_is_wider_than_the_tile_count() -> None:
    # 후보를 5개만 뽑으면 LLM이 그중 일부만 골라 타일이 비어 보인다.
    assert _HOME_CANDIDATE_POOL > 5


class StubPlannerLlm:
    """검색 계획이 항상 closet을 고르는 상황을 만든다."""

    async def plan_retrieval(self, **_kwargs) -> dict:
        return {
            "action": "retrieve",
            "retrieval_target": "closet",
            "candidate_scope": "all",
            "selected_item_refs": [],
            "reason": "stub",
        }


def run_planner(state: dict) -> dict:
    nodes = AgentNodes(llm_service=StubPlannerLlm())
    return asyncio.run(nodes.retrieval_planner_node(state))


def base_state(**overrides) -> dict:
    state = {
        "query": "오늘 뭐 입지",
        "resolved_query": "오늘 뭐 입지",
        "recommendation_target": "musinsa",
        "lock_retrieval_target": False,
    }
    state.update(overrides)
    return state


def test_locked_target_survives_the_planner() -> None:
    # 홈 타일은 사러 갈 상품을 보여주는 자리다. 계획이 closet을 골라도
    # 사용자가 이미 가진 옷이 올라오면 안 된다.
    result = run_planner(base_state(lock_retrieval_target=True))

    assert result["retrieval_target"] == "musinsa"


def test_planner_decides_when_the_target_is_not_locked() -> None:
    # 채팅에서는 "내 옷장에서 찾아줘"가 가능해야 하므로 계획을 따른다.
    result = run_planner(base_state(lock_retrieval_target=False))

    assert result["retrieval_target"] == "closet"


def rag_item(item_id: str, category: str, score: float) -> dict:
    return {
        "item_id": item_id,
        "source": "musinsa",
        "category": category,
        "image_url": f"https://image.example/{item_id}.jpg",
        "product_url": f"https://www.musinsa.com/products/{item_id}",
        "final_score": score,
    }


def rank(items: list[dict], diversify: bool) -> list[str]:
    nodes = AgentNodes()
    state = {
        "rag_results": items,
        "diversify_by_category": diversify,
        "use_closet_style": False,
    }
    result = asyncio.run(nodes.style_ranker_node(state))
    return [item["category"] for item in result["ranked_items"]]


SKEWED = [
    rag_item("a1", "아우터", 0.99),
    rag_item("a2", "아우터", 0.98),
    rag_item("a3", "아우터", 0.97),
    rag_item("t1", "상의", 0.80),
    rag_item("p1", "바지", 0.70),
]


def test_home_spreads_candidates_across_categories() -> None:
    # 겨울 검정 스트릿처럼 쏠린 취향이면 상위 후보가 전부 아우터가 된다.
    # 타일이 같은 종류로만 차는 것을 막는다.
    assert rank(SKEWED, diversify=True)[:3] == ["아우터", "상의", "바지"]


def test_chat_keeps_pure_score_order() -> None:
    # 채팅은 "바지 추천해줘"처럼 한 카테고리를 원하는 경우가 많다. 섞지 않는다.
    assert rank(SKEWED, diversify=False)[:3] == ["아우터", "아우터", "아우터"]


def test_spreading_keeps_every_candidate() -> None:
    assert len(rank(SKEWED, diversify=True)) == len(SKEWED)


def test_home_asks_for_more_tiles_than_the_chat_default() -> None:
    # 홈은 타일을 채워야 하는 화면이라 채팅 기본값(5)보다 많이 요청한다.
    from app.api.v1.recommendations import _HOME_CURATED_COUNT
    from app.services.llm_service import MAX_RECOMMENDATIONS

    assert _HOME_CURATED_COUNT > MAX_RECOMMENDATIONS


def test_home_candidate_pool_leaves_room_to_choose() -> None:
    # 후보가 피드 크기와 같으면 고르는 게 아니라 그대로 옮겨 적게 된다.
    from app.api.v1.recommendations import _HOME_CANDIDATE_POOL, _HOME_FEED_SIZE

    assert _HOME_CANDIDATE_POOL >= _HOME_FEED_SIZE * 2


def test_home_feed_leaves_every_category_something_to_show() -> None:
    # 칩을 클라이언트에서 거르는 구조다. 카테고리마다 두 줄(4칸)은 남지 않으면
    # 칩을 눌렀을 때 타일 한두 개짜리 화면이 나온다.
    from app.api.v1.recommendations import _HOME_CATEGORIES, _HOME_FEED_SIZE

    assert _HOME_FEED_SIZE >= len(_HOME_CATEGORIES) * 4


def test_home_feed_size_fills_the_last_row() -> None:
    # 프론트가 2열 그리드라 홀수면 마지막 줄이 반만 찬다.
    from app.api.v1.recommendations import _HOME_FEED_SIZE

    assert _HOME_FEED_SIZE % 2 == 0


def curated(count: int) -> list[dict]:
    return [
        {
            "item_id": f"curated_{index}",
            "source": "musinsa",
            "item_name": f"큐레이션 {index}",
            "brand": "brand",
            "category": "상의",
            "image_url": f"https://img/curated_{index}.jpg",
            "product_url": f"https://shop/curated_{index}",
            "price": 10000,
            "reason": "LLM이 쓴 이유",
        }
        for index in range(count)
    ]


def ranked(categories: list[str], per_category: int = 10, **overrides) -> list[dict]:
    items = []
    for category in categories:
        for index in range(per_category):
            item = {
                "item_id": f"{category}_{index}",
                "source": "musinsa",
                "item_name": f"{category} {index}",
                "brand": "brand",
                "category": category,
                "image_url": f"https://img/{category}_{index}.jpg",
                "product_url": f"https://shop/{category}_{index}",
                "price": 20000,
            }
            item.update(overrides)
            items.append(item)
    return items


def fill(recommendations: list[dict], ranked_items: list[dict], status: str = "success") -> dict:
    from app.api.v1.recommendations import _fill_home_feed

    response = {
        "status": status,
        "message": "메시지",
        "recommendations": recommendations,
        "style_guide": {"summary": "요약", "tips": []},
    }
    return _fill_home_feed(response, ranked_items)


def test_feed_fills_up_to_the_target_size() -> None:
    # LLM은 8개만 쓴다. 나머지는 이미 뽑아 둔 검색 결과로 채워야 칩이 걸린다.
    from app.api.v1.recommendations import _HOME_FEED_SIZE

    result = fill(curated(8), ranked(["상의", "바지", "아우터", "신발"]))

    assert len(result["recommendations"]) == _HOME_FEED_SIZE


def test_feed_keeps_the_curated_tiles_in_front() -> None:
    # 이유가 붙은 타일이 뒤로 밀리면 사용자는 AI가 고른 것을 보지 못한다.
    result = fill(curated(8), ranked(["바지", "아우터"]))

    assert [item["item_id"] for item in result["recommendations"][:8]] == [
        f"curated_{index}" for index in range(8)
    ]


def test_filled_tiles_carry_no_invented_reason() -> None:
    # 검색·랭킹이 그대로 실은 상품이다. 이유를 지어 붙이면 거짓말이 된다.
    result = fill(curated(8), ranked(["바지", "아우터"]))

    assert all(item["reason"] == "" for item in result["recommendations"][8:])
    assert all(item["reason"] for item in result["recommendations"][:8])


def test_feed_spreads_the_filled_tiles_across_categories() -> None:
    # 점수순으로만 자르면 상위가 한 종류로 쏠려 다른 칩이 빈 화면을 낸다.
    from collections import Counter

    result = fill([], ranked(["상의", "바지", "아우터", "신발", "가방", "모자"]))
    counts = Counter(item["category"] for item in result["recommendations"])

    assert len(counts) == 6
    assert max(counts.values()) - min(counts.values()) <= 1


def test_feed_does_not_repeat_a_curated_tile() -> None:
    # 랭킹 결과에는 LLM 이유가 붙은 상위 상품도 그대로 있다. 그냥 붙이면 중복된다.
    curated_tiles = curated(3)
    also_ranked = [
        {**tile, "reason": None} for tile in curated_tiles
    ] + ranked(["바지"], per_category=5)

    result = fill(curated_tiles, also_ranked)
    ids = [item["item_id"] for item in result["recommendations"]]

    assert len(ids) == len(set(ids))


def test_feed_drops_products_without_a_product_url() -> None:
    # RecommendationItem이 무신사 상품에 product_url을 요구한다. 하나라도 비면
    # 응답 전체가 검증에서 떨어져 홈이 통째로 실패한다.
    result = fill([], ranked(["바지"], per_category=5, product_url=None))

    assert result["recommendations"] == []


def test_filled_feed_still_validates_as_a_response() -> None:
    from app.schemas.recommendation import AgentResponse

    result = fill(curated(8), ranked(["상의", "바지", "아우터"]))

    assert len(AgentResponse.model_validate(result).recommendations) > 8


def test_feed_leaves_a_failed_response_alone() -> None:
    # 빈 응답에 검색 결과를 채우면 "못 찾았다"가 "찾았다"로 뒤집힌다.
    result = fill([], ranked(["바지"]), status="empty")

    assert result["recommendations"] == []


def run_pipeline(**kwargs) -> dict:
    from tests.test_agent_pipeline import build_pipeline

    pipeline, _vlm, _rag, llm = build_pipeline()
    asyncio.run(
        pipeline.run(query="화이트 니트랑 어울리는 바지 추천해줘", user_id="user_001", **kwargs)
    )
    return llm.calls[-1]


def test_requested_tile_count_reaches_the_llm() -> None:
    # 홈 엔드포인트에서 개수를 넘겨도 노드가 흘려보내지 않으면 5개로 잘린다.
    assert run_pipeline(max_recommendations=7)["max_recommendations"] == 7


def test_chat_leaves_the_count_to_the_llm_default() -> None:
    assert run_pipeline()["max_recommendations"] is None


def build_home(**overrides) -> dict:
    """홈 엔드포인트가 파이프라인에 넘길 인자를 만든다. DB는 더블로 대체한다."""
    import asyncio as _asyncio

    from app.api.v1 import recommendations as home_api

    class StubPreference:
        styles = ["스트릿", "캐주얼"]
        sizes = {"age_range": "20대"}
        gender = "men"
        height_cm = 178

    class StubUserService:
        async def get_preference(self, _db, _user):
            return StubPreference()

    class StubClosetService:
        async def list_for_user(self, _db, _user):
            return []

        def to_agent_payload(self, item):
            return item

    class StubLikeService:
        async def list_style_payloads(self, _db, _user_id):
            return [
                {
                    "product_ref": "liked_001",
                    "source": "musinsa",
                    "name": "스트릿 팬츠",
                }
            ]

    class StubUser:
        id = "user_001"

    original_user_service = home_api.UserService
    original_closet_service = home_api.ClosetService
    original_like_service = home_api.LikeService
    home_api.UserService = StubUserService
    home_api.ClosetService = StubClosetService
    home_api.LikeService = StubLikeService
    try:
        kwargs = {"prompt": "", "refresh_seed": 0, "category": "", "mood": "", "season": ""}
        kwargs.update(overrides)
        return _asyncio.run(home_api._build_home_request(None, StubUser(), **kwargs))
    finally:
        home_api.UserService = original_user_service
        home_api.ClosetService = original_closet_service
        home_api.LikeService = original_like_service


def call_home(**overrides) -> tuple[dict, dict]:
    """`/home` 응답과 파이프라인이 받은 인자를 함께 돌려준다."""
    import asyncio as _asyncio

    from app.api.v1 import recommendations as home_api

    class StubPreference:
        styles = ["스트릿"]
        sizes = {"age_range": "20대"}
        gender = "men"
        height_cm = 178

    class StubUserService:
        async def get_preference(self, _db, _user):
            return StubPreference()

    class StubClosetService:
        async def list_for_user(self, _db, _user):
            return []

        def to_agent_payload(self, item):
            return item

    class StubLikeService:
        async def list_style_payloads(self, _db, _user_id):
            return [
                {
                    "product_ref": "liked_001",
                    "source": "musinsa",
                    "name": "스트릿 팬츠",
                }
            ]

    class StubUser:
        id = "user_001"

    seen: dict = {}

    class StubRecommendationService:
        async def create_home(self, **kwargs):
            seen.update(kwargs)
            return {
                "response": {
                    "status": "success",
                    "message": "추천이에요",
                    "recommendations": curated(2),
                    "style_guide": {"summary": "요약", "tips": []},
                },
                # 피드 크기보다 넉넉해야 "채우다 만" 결과와 구분된다.
                "ranked_items": ranked(["상의", "바지", "아우터"], per_category=20),
            }

    originals = (
        home_api.UserService,
        home_api.ClosetService,
        home_api.LikeService,
        home_api.RecommendationService,
    )
    home_api.UserService = StubUserService
    home_api.ClosetService = StubClosetService
    home_api.LikeService = StubLikeService
    home_api.RecommendationService = StubRecommendationService
    try:
        kwargs = {"prompt": "", "refresh_seed": 0, "category": "", "mood": "", "season": ""}
        kwargs.update(overrides)
        response = _asyncio.run(
            home_api.get_home_recommendation(current_user=StubUser(), db=None, **kwargs)
        )
    finally:
        (
            home_api.UserService,
            home_api.ClosetService,
            home_api.LikeService,
            home_api.RecommendationService,
        ) = originals

    return response.model_dump(), seen


def test_home_fills_the_feed_from_the_ranked_leftovers() -> None:
    # LLM은 앞쪽 몇 칸만 쓴다. 나머지를 안 채우면 칩이 거를 타일이 없다.
    from app.api.v1.recommendations import _HOME_FEED_SIZE

    result, _ = call_home()

    assert len(result["recommendations"]) == _HOME_FEED_SIZE


def test_home_asks_the_workflow_for_the_leftovers() -> None:
    # 트레이스를 요청하지 않으면 랭킹 결과가 없어 피드를 채울 재료가 없다.
    _result, received = call_home()

    assert received["return_trace"] is True


def test_home_sends_saved_likes_to_the_personalization_ranker() -> None:
    request = build_home()

    assert request["run_kwargs"]["liked_items"][0]["product_ref"] == "liked_001"


def test_home_counts_the_filled_feed() -> None:
    # 큐레이션 개수만 세면 화면에 보이는 타일 수와 어긋난다.
    result, _ = call_home()

    assert result["applied_filters"]["result_count"] == len(result["recommendations"])


def test_category_chip_becomes_a_real_filter() -> None:
    # 칩을 검색어에 문자열로 합쳐 보내면 벡터 유사도에 묻힌다. 필터로 박아야 한다.
    request = build_home(category="바지")

    assert request["run_kwargs"]["context"]["category"] == "바지"


def test_unknown_category_is_ignored_rather_than_rejected() -> None:
    # 필터 하나 때문에 홈이 통째로 비는 것보다 무시하는 편이 낫다.
    request = build_home(category="양말")

    assert "category" not in request["run_kwargs"]["context"]
    assert request["applied_filters"]["category"] is None


def test_choosing_a_category_turns_off_diversification() -> None:
    # "바지"를 골랐는데 여러 종류로 섞어 주면 고른 의미가 없다.
    assert build_home(category="바지")["run_kwargs"]["diversify_by_category"] is False


def test_no_category_keeps_diversification_on() -> None:
    assert build_home()["run_kwargs"]["diversify_by_category"] is True


def test_mood_and_season_reach_the_context() -> None:
    request = build_home(mood="STREET", season="Summer")

    assert request["run_kwargs"]["context"]["mood"] == "street"
    assert request["run_kwargs"]["context"]["season"] == "summer"


def test_unknown_mood_and_season_are_dropped() -> None:
    request = build_home(mood="힙합", season="장마")

    assert "mood" not in request["run_kwargs"]["context"]
    assert "season" not in request["run_kwargs"]["context"]


def test_applied_filters_report_what_was_used() -> None:
    # 사용자가 결과의 근거를 볼 수 있어야 한다.
    applied = build_home(category="바지", season="summer", prompt="청바지")["applied_filters"]

    assert applied["category"] == "바지"
    assert applied["season"] == "summer"
    assert applied["prompt"] == "청바지"
    assert applied["age_range"] == "20대"
    assert applied["preferred_styles"] == ["스트릿", "캐주얼"]


def test_refresh_seed_is_carried_into_the_search() -> None:
    assert build_home(refresh_seed=3)["run_kwargs"]["context"]["refresh_seed"] == 3


def test_chip_category_beats_the_inferred_one() -> None:
    # "상의"를 검색창에 적고 "바지" 칩을 눌렀다면 누른 쪽이 이겨야 한다.
    request = build_home(category="바지", prompt="상의 추천해줘")

    assert request["run_kwargs"]["context"]["category"] == "바지"


def test_explicit_home_filter_keeps_taste_out_of_the_search_query() -> None:
    request = build_home(category="바지")
    query = request["run_kwargs"]["query"]

    assert "스트릿" not in query
    assert "black" not in query
