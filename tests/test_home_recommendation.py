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


def test_home_query_appends_the_user_prompt() -> None:
    query = home_query(prompt="비 오는 날 입을 옷")

    assert query.endswith("비 오는 날 입을 옷")


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
