import asyncio

from app.agent.agent_pipeline import AidFitAgentPipeline
from app.agent.nodes import AgentNodes


class FakeVlmService:
    # Captures VLM calls while returning deterministic image metadata.
    def __init__(self, response: dict | None = None, is_fashion_item: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.response = response
        self.is_fashion_item = is_fashion_item

    async def analyze_many(self, image_urls: list[str]) -> dict:
        self.calls.append(image_urls)
        if self.response is not None:
            return self.response
        return {
            "items": [
                {
                    "category": "top",
                    "color": "white",
                    "material": "knit",
                    "fit": "oversized",
                    "mood": "minimal",
                    "sense_of_season": "spring",
                }
            ],
            "is_fashion_item": self.is_fashion_item,
        }


class FakeRagService:
    # Captures RAG requests so tests can assert routing and filter behavior.
    def __init__(self, items: list[dict] | None = None, response: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.items = items if items is not None else [fake_item("item_1", final_score=0.5)]
        self.response = response

    async def search_request(self, rag_request: dict) -> dict:
        self.calls.append(rag_request)
        if self.response is not None:
            return self.response
        return {"items": self.items, "message": "success" if self.items else "검색 결과가 없습니다."}


class EmptyThenResultRagService:
    # Simulates a retry path where fallback retrieval recovers results.
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def search_request(self, rag_request: dict) -> dict:
        self.calls.append(rag_request)
        if len(self.calls) == 1:
            return {"items": [], "message": "검색 결과가 없습니다."}
        return {"items": [fake_item("fallback_item", final_score=0.2)], "message": "success"}


class FakeLlmService:
    # Captures final generation inputs without calling an external model.
    def __init__(self, response: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.response = response

    async def compose_recommendation(
        self,
        query: str,
        vlm_items: list[dict],
        ranked_items: list[dict],
        retrieval_target: str = "musinsa",
        closet_items: list[dict] | None = None,
        use_closet_style: bool = True,
        user_profile: dict | None = None,
        chat_history: list[dict] | None = None,
    ) -> dict:
        self.calls.append(
            {
                "query": query,
                "vlm_items": vlm_items,
                "ranked_items": ranked_items,
                "retrieval_target": retrieval_target,
                "closet_items": closet_items or [],
                "use_closet_style": use_closet_style,
                "user_profile": user_profile or {},
                "chat_history": chat_history or [],
            }
        )
        if self.response is not None:
            return self.response
        if not ranked_items:
            return {
                "status": "empty",
                "message": "조건에 맞는 추천 상품을 찾지 못했습니다.",
                "recommendations": [],
                "style_guide": None,
            }
        return {
            "status": "success",
            "message": f"{retrieval_target} recommendation",
            "recommendations": [
                {
                    "item_id": item["item_id"],
                    "source": item["source"],
                    "item_name": item["name"],
                    "brand": item["brand"],
                    "category": item["category"],
                    "image_url": item["image_url"],
                    "product_url": item["product_url"],
                    "price": item["price"],
                    "reason": "query와 후보 상품이 잘 맞습니다.",
                }
                for item in ranked_items[:5]
            ],
            "style_guide": {"summary": "테스트 코디", "tips": []},
        }


def fake_item(
    item_id: str,
    final_score: float | None = None,
    metadata_score: float | None = None,
    mood: str | None = None,
    color: str | None = None,
    category: str = "pants",
    sense_of_season: str | None = None,
) -> dict:
    # Build the smallest valid Musinsa-like item needed by agent tests.
    return {
        "item_id": item_id,
        "source": "musinsa",
        "name": "세미 와이드 데님 팬츠",
        "brand": "Example Brand",
        "price": 59000,
        "category": category,
        "image_url": "https://image.msscdn.net/images/goods_img/example.jpg",
        "product_url": f"https://www.musinsa.com/products/{item_id}",
        "color": color,
        "mood": mood,
        "sense_of_season": sense_of_season,
        "similarity_score": 0.1,
        "metadata_score": metadata_score,
        "final_score": final_score,
    }


def build_pipeline(
    vlm_service: FakeVlmService | None = None,
    rag_service: FakeRagService | EmptyThenResultRagService | None = None,
    llm_service: FakeLlmService | None = None,
) -> tuple[AidFitAgentPipeline, FakeVlmService, FakeRagService | EmptyThenResultRagService, FakeLlmService]:
    vlm = vlm_service or FakeVlmService()
    rag = rag_service or FakeRagService()
    llm = llm_service or FakeLlmService()
    return AidFitAgentPipeline(AgentNodes(vlm, rag, llm)), vlm, rag, llm


def test_text_only_skips_vlm_and_routes_to_musinsa() -> None:
    pipeline, vlm, rag, llm = build_pipeline()

    result = asyncio.run(pipeline.run(query="화이트 니트랑 어울리는 바지 추천해줘", user_id="user_001"))

    assert result["status"] == "success"
    assert vlm.calls == []
    assert rag.calls[0]["retrieval_target"] == "musinsa"
    assert "vlm_items" in rag.calls[0]
    assert "items" not in rag.calls[0]
    assert llm.calls[0]["retrieval_target"] == "musinsa"


def test_image_input_runs_vlm_before_rag() -> None:
    pipeline, vlm, rag, llm = build_pipeline()

    result = asyncio.run(
        pipeline.run(
            query="이 상의와 어울리는 바지 추천해줘",
            user_id="user_001",
            image_urls=["https://cdn.aidfit.com/item_001.jpg"],
        )
    )

    assert result["status"] == "success"
    assert vlm.calls == [["https://cdn.aidfit.com/item_001.jpg"]]
    assert rag.calls[0]["vlm_items"][0]["color"] == "white"
    assert llm.calls[0]["vlm_items"][0]["material"] == "knit"


def test_closet_items_default_to_hybrid() -> None:
    pipeline, _, rag, llm = build_pipeline()

    asyncio.run(
        pipeline.run(
            query="이 가방이랑 어울리는 상의 추천해줘",
            user_id="user_001",
            closet_items=[{"closet_item_id": "closet_001", "category": "bag"}],
        )
    )

    assert rag.calls[0]["retrieval_target"] == "hybrid"
    assert llm.calls[0]["retrieval_target"] == "hybrid"


def test_profile_context_with_closet_style_defaults_to_hybrid() -> None:
    pipeline, _, rag, llm = build_pipeline()

    asyncio.run(
        pipeline.run(
            query="데일리로 입기 좋은 바지 추천해줘",
            user_id="user_001",
            user_profile={"preferred_styles": ["minimal", "casual"]},
            use_closet_style=True,
        )
    )

    assert rag.calls[0]["retrieval_target"] == "hybrid"
    assert llm.calls[0]["retrieval_target"] == "hybrid"


def test_profile_context_without_closet_style_defaults_to_musinsa() -> None:
    pipeline, _, rag, llm = build_pipeline()

    asyncio.run(
        pipeline.run(
            query="데일리로 입기 좋은 바지 추천해줘",
            user_id="user_001",
            user_profile={"preferred_styles": ["minimal", "casual"]},
            use_closet_style=False,
        )
    )

    assert rag.calls[0]["retrieval_target"] == "musinsa"
    assert llm.calls[0]["retrieval_target"] == "musinsa"


def test_vlm_closet_signal_routes_to_hybrid() -> None:
    vlm_response = {
        "items": [
            {
                "product_url": "closet://closet_001",
                "category": "top",
                "color": "white",
            }
        ],
        "is_fashion_item": True,
    }
    pipeline, _, rag, _ = build_pipeline(vlm_service=FakeVlmService(response=vlm_response))

    asyncio.run(
        pipeline.run(
            query="이 상의에 어울리는 바지 추천해줘",
            user_id="user_001",
            image_urls=["https://cdn.aidfit.com/closet_001.jpg"],
        )
    )

    assert rag.calls[0]["retrieval_target"] == "hybrid"


def test_closet_only_query_routes_to_closet() -> None:
    pipeline, _, rag, _ = build_pipeline()

    asyncio.run(
        pipeline.run(
            query="내 옷장 안에서만 코디 추천해줘",
            user_id="user_001",
            closet_items=[{"closet_item_id": "closet_001", "category": "bag"}],
        )
    )

    assert rag.calls[0]["retrieval_target"] == "closet"


def test_musinsa_query_routes_to_musinsa() -> None:
    pipeline, _, rag, _ = build_pipeline()

    asyncio.run(
        pipeline.run(
            query="무신사에서 살 만한 상품 추천해줘",
            user_id="user_001",
            closet_items=[{"closet_item_id": "closet_001", "category": "bag"}],
        )
    )

    assert rag.calls[0]["retrieval_target"] == "musinsa"


def test_empty_query_returns_public_error_contract() -> None:
    pipeline, _, rag, _ = build_pipeline()

    result = asyncio.run(pipeline.run(query="", user_id="user_001"))

    assert result == {
        "status": "error",
        "message": "사용자 요청(query)이 비어 있습니다.",
        "recommendations": [],
        "style_guide": None,
    }
    assert rag.calls == []


def test_non_fashion_image_stops_before_rag() -> None:
    pipeline, vlm, rag, _ = build_pipeline(vlm_service=FakeVlmService(is_fashion_item=False))

    result = asyncio.run(
        pipeline.run(
            query="이 이미지와 어울리는 옷 추천해줘",
            user_id="user_001",
            image_urls=["mock://food-image"],
        )
    )

    assert result["status"] == "error"
    assert "의류 아이템" in result["message"]
    assert vlm.calls == [["mock://food-image"]]
    assert rag.calls == []


def test_rag_request_adds_profile_and_vlm_filter_candidates() -> None:
    pipeline, _, rag, _ = build_pipeline()

    asyncio.run(
        pipeline.run(
            query="이 상의와 어울리는 바지 추천해줘",
            user_id="user_001",
            image_urls=["https://cdn.aidfit.com/item_001.jpg"],
            user_profile={"preferred_styles": ["minimal", "casual"]},
        )
    )

    filters = rag.calls[0]["filters"]
    assert filters["preferred_styles"] == ["minimal", "casual"]
    assert filters["sense_of_season"] == "spring"
    assert filters["category"] == "top"
    assert filters["color"] == "white"


def test_context_filters_override_inferred_vlm_filters() -> None:
    pipeline, _, rag, _ = build_pipeline()

    asyncio.run(
        pipeline.run(
            query="이 상의와 어울리는 바지 추천해줘",
            user_id="user_001",
            image_urls=["https://cdn.aidfit.com/item_001.jpg"],
            user_profile={"preferred_styles": ["minimal"]},
            context={"category": "pants", "color": "blue", "preferred_styles": ["street"]},
        )
    )

    filters = rag.calls[0]["filters"]
    assert filters["category"] == "pants"
    assert filters["color"] == "blue"
    assert filters["preferred_styles"] == ["street"]
    assert filters["sense_of_season"] == "spring"


def test_ranking_prefers_user_profile_mood_when_base_scores_match() -> None:
    items = [
        fake_item("street_item", final_score=0.5, mood="street"),
        fake_item("minimal_item", final_score=0.5, mood="minimal"),
    ]
    pipeline, _, _, llm = build_pipeline(rag_service=FakeRagService(items=items))

    asyncio.run(
        pipeline.run(
            query="바지 추천해줘",
            user_id="user_001",
            user_profile={"preferred_styles": ["minimal"]},
        )
    )

    assert llm.calls[0]["ranked_items"][0]["item_id"] == "minimal_item"


def test_ranking_boosts_closet_metadata_matches() -> None:
    items = [
        fake_item("blue_winter", final_score=0.5, color="blue", mood="street", sense_of_season="winter"),
        fake_item("black_summer", final_score=0.5, color="black", mood="street", sense_of_season="summer"),
    ]
    pipeline, _, _, llm = build_pipeline(rag_service=FakeRagService(items=items))

    asyncio.run(
        pipeline.run(
            query="이 가방이랑 어울리는 바지 추천해줘",
            user_id="user_001",
            closet_items=[
                {
                    "closet_item_id": "closet_001",
                    "category": "bag",
                    "color": "black",
                    "mood": "street",
                    "sense_of_season": "summer",
                }
            ],
        )
    )

    assert llm.calls[0]["ranked_items"][0]["item_id"] == "black_summer"
    assert llm.calls[0]["closet_items"][0]["closet_item_id"] == "closet_001"
    assert llm.calls[0]["use_closet_style"] is True


def test_use_closet_style_false_ignores_user_profile_boost() -> None:
    items = [
        fake_item("query_item", final_score=0.5, color="white", mood="casual"),
        fake_item("profile_item", final_score=0.5, color="black", mood="minimal"),
    ]
    pipeline, _, _, llm = build_pipeline(rag_service=FakeRagService(items=items))

    asyncio.run(
        pipeline.run(
            query="화이트 바지 추천해줘",
            user_id="user_001",
            use_closet_style=False,
            user_profile={"preferred_styles": ["minimal"]},
        )
    )

    assert llm.calls[0]["ranked_items"][0]["item_id"] == "query_item"
    assert llm.calls[0]["use_closet_style"] is False
    assert llm.calls[0]["user_profile"] == {"preferred_styles": ["minimal"]}


def test_rag_empty_result_fallback_runs_once_then_returns_empty() -> None:
    pipeline, _, rag, _ = build_pipeline(rag_service=FakeRagService(items=[]))

    result = asyncio.run(
        pipeline.run(
            query="화이트 니트랑 어울리는 바지 추천해줘",
            user_id="user_001",
            context={"season": "spring", "style": "minimal", "preferred_styles": ["minimal"], "top_k": 3},
        )
    )

    assert result["status"] == "empty"
    assert len(rag.calls) == 2
    assert rag.calls[0]["filters"]["season"] == "spring"
    assert "season" not in rag.calls[1]["filters"]
    assert "style" not in rag.calls[1]["filters"]
    assert rag.calls[1]["filters"]["preferred_styles"] == ["minimal"]
    assert rag.calls[1]["top_k"] == 20


def test_rag_empty_result_fallback_can_recover() -> None:
    pipeline, _, rag, _ = build_pipeline(rag_service=EmptyThenResultRagService())

    result = asyncio.run(pipeline.run(query="화이트 니트랑 어울리는 바지 추천해줘", user_id="user_001"))

    assert result["status"] == "success"
    assert len(rag.calls) == 2


def test_invalid_vlm_response_becomes_public_error() -> None:
    pipeline, _, rag, _ = build_pipeline(vlm_service=FakeVlmService(response={"items": "invalid"}))

    result = asyncio.run(
        pipeline.run(query="이 이미지와 어울리는 옷 추천해줘", user_id="user_001", image_urls=["mock://top"])
    )

    assert result["status"] == "error"
    assert result["message"] == "이미지 분석 결과 형식이 올바르지 않습니다."
    assert rag.calls == []


def test_invalid_rag_response_becomes_public_error() -> None:
    rag = FakeRagService(response={"items": [{"source": "musinsa", "image_url": "https://image.example/item.jpg"}]})
    pipeline, _, _, _ = build_pipeline(rag_service=rag)

    result = asyncio.run(pipeline.run(query="화이트 니트랑 어울리는 바지 추천해줘", user_id="user_001"))

    assert result["status"] == "error"
    assert result["message"] == "추천 상품 검색 결과 형식이 올바르지 않습니다."


def test_invalid_llm_response_becomes_public_error() -> None:
    llm = FakeLlmService(
        response={
            "status": "success",
            "message": "invalid recommendation",
            "recommendations": [
                {
                    "source": "musinsa",
                    "image_url": "https://image.example/item.jpg",
                    "reason": "product_url is missing",
                }
            ],
            "style_guide": None,
        }
    )
    pipeline, _, _, _ = build_pipeline(llm_service=llm)

    result = asyncio.run(pipeline.run(query="화이트 니트랑 어울리는 바지 추천해줘", user_id="user_001"))

    assert result["status"] == "error"
    assert result["message"] == "최종 추천 결과 형식이 올바르지 않습니다."


def outfit_vlm_response(*items: dict) -> dict:
    return {"items": list(items), "is_fashion_item": True}


def test_outfit_photo_does_not_narrow_filters_to_one_garment() -> None:
    # A full-body photo has no single category or color, so neither may filter retrieval.
    vlm_response = outfit_vlm_response(
        {"category": "아우터", "color": "blue", "sense_of_season": "fall"},
        {"category": "바지", "color": "black", "sense_of_season": "summer"},
        {"category": "신발", "color": "black", "sense_of_season": "fall"},
    )
    pipeline, _, rag, _ = build_pipeline(vlm_service=FakeVlmService(response=vlm_response))

    asyncio.run(
        pipeline.run(
            query="이 코디에 어울리는 가방 추천해줘",
            user_id="user_001",
            image_urls=["https://cdn.aidfit.com/outfit_001.jpg"],
        )
    )

    filters = rag.calls[0]["filters"]
    assert "category" not in filters
    assert "color" not in filters
    # Seasons disagree here, so no season filter either.
    assert "sense_of_season" not in filters
    # Every garment still reaches retrieval as context.
    assert len(rag.calls[0]["vlm_items"]) == 3


def test_outfit_photo_keeps_a_unanimous_season_filter() -> None:
    vlm_response = outfit_vlm_response(
        {"category": "아우터", "color": "blue", "sense_of_season": "fall"},
        {"category": "바지", "color": "black", "sense_of_season": "fall"},
    )
    pipeline, _, rag, _ = build_pipeline(vlm_service=FakeVlmService(response=vlm_response))

    asyncio.run(
        pipeline.run(
            query="이 코디에 어울리는 신발 추천해줘",
            user_id="user_001",
            image_urls=["https://cdn.aidfit.com/outfit_001.jpg"],
        )
    )

    assert rag.calls[0]["filters"]["sense_of_season"] == "fall"


def test_outfit_items_all_reach_the_rag_query_and_llm() -> None:
    vlm_response = outfit_vlm_response(
        {"category": "아우터", "color": "blue", "material": "denim"},
        {"category": "바지", "color": "black", "material": "corduroy"},
    )
    pipeline, _, rag, llm = build_pipeline(vlm_service=FakeVlmService(response=vlm_response))

    asyncio.run(
        pipeline.run(
            query="이 코디에 어울리는 모자 추천해줘",
            user_id="user_001",
            image_urls=["https://cdn.aidfit.com/outfit_001.jpg"],
        )
    )

    rag_query = rag.calls[0]["query"]
    assert "denim" in rag_query and "corduroy" in rag_query
    assert len(llm.calls[0]["vlm_items"]) == 2


def test_context_filters_still_win_over_outfit_inference() -> None:
    vlm_response = outfit_vlm_response(
        {"category": "아우터", "color": "blue", "sense_of_season": "fall"},
        {"category": "바지", "color": "black", "sense_of_season": "summer"},
    )
    pipeline, _, rag, _ = build_pipeline(vlm_service=FakeVlmService(response=vlm_response))

    asyncio.run(
        pipeline.run(
            query="이 코디에 어울리는 신발 추천해줘",
            user_id="user_001",
            image_urls=["https://cdn.aidfit.com/outfit_001.jpg"],
            context={"category": "신발", "season": "fall"},
        )
    )

    filters = rag.calls[0]["filters"]
    assert filters["category"] == "신발"
    assert filters["season"] == "fall"

def test_chat_history_reaches_final_response_generation() -> None:
    # 후속 질문("더 저렴한 걸로")은 직전 대화가 있어야 이해된다.
    pipeline, _, _, llm = build_pipeline()
    history = [
        {"role": "user", "content": "검은색 재킷에 어울리는 바지 추천해줘"},
        {"role": "assistant", "content": "회색 와이드 슬랙스를 추천합니다."},
    ]

    asyncio.run(
        pipeline.run(
            query="조금 더 저렴한 제품으로 추천해줘",
            user_id="user_001",
            chat_history=history,
        )
    )

    assert llm.calls[0]["chat_history"] == history


def test_chat_history_defaults_to_empty_for_one_off_requests() -> None:
    pipeline, _, _, llm = build_pipeline()

    asyncio.run(pipeline.run(query="화이트 니트랑 어울리는 바지 추천해줘", user_id="user_001"))

    assert llm.calls[0]["chat_history"] == []

