"""네트워크 없이 도는 AI 서비스 더블.

프로덕션에 있던 `USE_MOCK_AI` 분기를 걷어내면서, 그 결정적 구현을 여기로
옮겨 왔다. 배포 코드에 테스트용 분기를 남겨 두면 실제 경로와 목업 경로가
서로 다르게 굳는다 — 목업에서만 통과하는 버그가 그렇게 생긴다.

더블은 **모델을 부르는 지점 하나만** 가로챈다. 응답을 검증하고 다듬는
뒷부분은 프로덕션 코드가 그대로 돈다. 그래야 테스트가 실제로 도는 코드를
확인한다.
"""

from typing import Any

from app.schemas.ai import RAGRequest, VLMResponse
from app.services import llm_service as llm_module
from app.services.catalog_matching import normalize_category
from app.services.llm_service import (
    MAX_RECOMMENDATIONS,
    LlmService,
    build_conversation_query,
    build_rag_query,
)
from app.services.rag_service import RagService
from app.services.target_category import infer_target_category
from app.services.vlm_service import VlmService


class DeterministicLlmService(LlmService):
    """구조화 응답을 만드는 지점(`_generate_structured`)만 가로챈다."""

    async def _generate_structured(
        self,
        system_instruction: str,
        prompt: dict[str, Any],
        response_schema: dict[str, Any],
        temperature: float,
        model: str | None = None,
        thinking_budget: int | None = None,
    ) -> dict[str, Any]:
        if system_instruction is llm_module.INTENT_CLASSIFIER_SYSTEM_PROMPT:
            return self._mock_classify_intent(
                prompt["current_query"], prompt["chat_history"], prompt["has_attached_image"]
            )
        if system_instruction is llm_module.QUERY_REFINER_SYSTEM_PROMPT:
            return self._mock_refine_query(
                prompt["current_query"], prompt["chat_history"], prompt["vlm_items"]
            )
        if system_instruction is llm_module.RETRIEVAL_PLANNER_SYSTEM_PROMPT:
            return self._mock_plan_retrieval(
                query=prompt["refined_query"],
                original_query=prompt["current_query"],
                previous_items=prompt["previous_rag_items"],
                previous_retrieval_target=prompt["previous_retrieval_target"],
                closet_items=prompt["selected_closet_items"],
                user_profile=prompt["user_profile"],
                vlm_items=prompt["vlm_items"],
                use_closet_style=prompt["use_closet_style"],
            )
        if system_instruction is llm_module.GENERAL_CHAT_SYSTEM_PROMPT:
            return self._mock_general_chat(prompt["current_query"])
        raise AssertionError(f"더블이 모르는 LLM 호출이다: {system_instruction[:60]}")

    async def _external_compose_recommendation(
        self,
        query: str,
        vlm_items: list[dict],
        ranked_items: list[dict],
        retrieval_target: str = "musinsa",
        closet_items: list[dict] | None = None,
        use_closet_style: bool = True,
        user_profile: dict | None = None,
        chat_history: list[dict] | None = None,
        max_recommendations: int = 5,
    ) -> dict:
        return await self._mock_compose_recommendation(
            query, vlm_items, ranked_items, retrieval_target, max_recommendations
        )

    def _mock_classify_intent(
        self,
        query: str,
        chat_history: list[dict],
        has_image: bool,
    ) -> dict[str, Any]:
        """Offline-only stand-in; production routing uses the model above."""
        if has_image:
            return {"intent": "fashion_service", "reason": "attached image"}

        current = str(query or "").lower()
        fashion_terms = (
            "옷",
            "코디",
            "패션",
            "스타일",
            "상의",
            "하의",
            "바지",
            "팬츠",
            "셔츠",
            "니트",
            "재킷",
            "자켓",
            "원피스",
            "치마",
            "스커트",
            "신발",
            "가방",
            "모자",
            "무신사",
            "wardrobe",
            "outfit",
            "fashion",
            "clothing",
            "musinsa",
        )
        if any(term in current for term in fashion_terms):
            return {"intent": "fashion_service", "reason": "fashion request"}

        follow_up_terms = (
            "더 저렴",
            "더 비싼",
            "그중",
            "이 중",
            "첫 번째",
            "두 번째",
            "비슷한",
            "다른 걸",
            "하나 더",
            "그거",
            "이거",
        )
        if any(term in current for term in follow_up_terms):
            recent = " ".join(str(message.get("content") or "").lower() for message in chat_history[-4:])
            if any(term in recent for term in fashion_terms):
                return {"intent": "fashion_service", "reason": "fashion follow-up"}
        return {"intent": "general_chat", "reason": "ordinary conversation"}

    def _mock_refine_query(
        self,
        query: str,
        chat_history: list[dict],
        vlm_items: list[dict],
    ) -> dict[str, Any]:
        conversation_query = build_conversation_query(chat_history, query)
        lowered = str(query or "").lower()
        if any(term in lowered for term in ("비슷", "유사", "닮은", "같은 상품")):
            request_mode = "similarity"
        elif any(term in lowered for term in ("어울", "코디", "매치", "같이 입", "함께 입")):
            request_mode = "coordination"
        else:
            request_mode = "direct"

        target_category = infer_target_category(str(query or ""))
        if request_mode == "similarity" and target_category is None and len(vlm_items) == 1:
            target_category = normalize_category(vlm_items[0].get("category"))

        # Coordination queries are candidate-focused. Raw VLM terms are reference
        # metadata and deliberately do not get appended to the retrieval query.
        refined = (
            conversation_query
            if request_mode == "coordination"
            else build_rag_query({"items": vlm_items}, conversation_query)
        )
        return {
            "query": refined or str(query).strip(),
            "request_mode": request_mode,
            "target_category": target_category,
        }

    def _mock_plan_retrieval(
        self,
        query: str,
        original_query: str,
        previous_items: list[dict[str, Any]],
        previous_retrieval_target: str | None,
        closet_items: list[dict],
        user_profile: dict,
        vlm_items: list[dict],
        use_closet_style: bool,
    ) -> dict[str, Any]:
        """네트워크 없이 도는 결정적 검색 계획."""
        current = str(original_query or "").lower()
        resolved = str(query or "").lower()
        closet_terms = ("옷장", "내 옷으로", "내옷으로", "closet", "wardrobe")
        catalog_terms = ("무신사", "구매", "살 만한", "살만한", "상품", "buy", "musinsa")

        if any(term in current for term in closet_terms):
            target = "closet"
        elif any(term in current for term in catalog_terms):
            target = "musinsa"
        elif previous_retrieval_target in {"closet", "musinsa", "hybrid"} and previous_items:
            target = previous_retrieval_target
        elif any(term in resolved for term in closet_terms):
            target = "closet"
        elif any(term in resolved for term in catalog_terms):
            target = "musinsa"
        elif any(
            item.get("source") == "closet"
            or str(item.get("product_url") or "").startswith("closet://")
            or str(item.get("item_id") or item.get("closet_item_id") or "").startswith("closet")
            for item in vlm_items
        ):
            target = "hybrid"
        elif closet_items or (use_closet_style and any(bool(value) for value in user_profile.values())):
            target = "hybrid"
        else:
            target = "musinsa"

        requested_category = self._category_group(current)
        previous_categories = {
            category
            for item in previous_items
            if (category := self._category_group(str(item.get("category") or "")))
        }
        source_changed = (
            previous_retrieval_target in {"closet", "musinsa", "hybrid"}
            and target != previous_retrieval_target
            and any(term in current for term in (*closet_terms, *catalog_terms))
        )
        category_changed = bool(
            requested_category
            and previous_categories
            and requested_category not in previous_categories
        )
        if source_changed or category_changed:
            return {
                "action": "retrieve",
                "retrieval_target": target,
                "candidate_scope": "all",
                "selected_item_refs": [],
                "reason": "source or category changed",
            }

        if not previous_items:
            return {
                "action": "retrieve",
                "retrieval_target": target,
                "candidate_scope": "all",
                "selected_item_refs": [],
                "reason": "no previous candidates",
            }

        alternative_terms = (
            "다른",
            "새로운",
            "하나 더",
            "더 보여",
            "더 추천",
            "새로",
            "비슷한",
            "또 보여",
        )
        if any(term in current for term in alternative_terms):
            unseen_items = [item for item in previous_items if not item["was_shown"]]
            if unseen_items:
                return {
                    "action": "reuse",
                    "retrieval_target": target,
                    "candidate_scope": "unseen",
                    "selected_item_refs": [item["ref"] for item in unseen_items],
                    "reason": "unseen previous candidates are sufficient",
                }
            return {
                "action": "retrieve",
                "retrieval_target": target,
                "candidate_scope": "unseen",
                "selected_item_refs": [],
                "reason": "cached unseen candidates are exhausted",
            }

        explicit_reference_terms = (
            "이 중",
            "그중",
            "첫 번째",
            "두 번째",
            "설명",
            "어느",
            "뭐가",
            "방금",
            "아까",
        )
        comparison_terms = ("저렴", "비싼", "가격", "비교")
        should_reuse = any(term in current for term in (*explicit_reference_terms, *comparison_terms))
        if not should_reuse:
            return {
                "action": "retrieve",
                "retrieval_target": target,
                "candidate_scope": "all",
                "selected_item_refs": [],
                "reason": "new or insufficient request",
            }

        candidate_scope = "shown" if any(
            term in current for term in explicit_reference_terms
        ) and any(item["was_shown"] for item in previous_items) else "all"
        ordered = [
            item
            for item in previous_items
            if candidate_scope == "all" or item["was_shown"]
        ]
        if "저렴" in current:
            ordered.sort(key=lambda item: (item.get("price") is None, item.get("price") or 0))
        elif "비싼" in current:
            ordered.sort(key=lambda item: item.get("price") or -1, reverse=True)
        if "첫 번째" in current:
            ordered = ordered[:1]
        elif "두 번째" in current:
            ordered = ordered[1:2]

        return {
            "action": "reuse",
            "retrieval_target": target,
            "candidate_scope": candidate_scope,
            "selected_item_refs": [item["ref"] for item in ordered],
            "reason": "previous candidates are sufficient",
        }

    def _mock_general_chat(self, query: str) -> dict[str, str]:
        current = str(query or "").strip()
        if any(greeting in current.lower() for greeting in ("안녕", "hello", "hi")):
            message = "안녕하세요! 무엇을 도와드릴까요?"
        elif "고마" in current.lower():
            message = "천만에요. 필요할 때 언제든 말씀해주세요!"
        else:
            message = "말씀하신 내용을 확인했어요. 궁금한 점을 조금 더 구체적으로 알려주세요."
        return {"message": message}

    async def _mock_compose_recommendation(
        self,
        query: str,
        vlm_items: list[dict],
        ranked_items: list[dict],
        retrieval_target: str = "musinsa",
        max_recommendations: int = MAX_RECOMMENDATIONS,
    ) -> dict:
        if not ranked_items:
            return self._empty_response()

        base_item = vlm_items[0] if vlm_items else {}
        recommendations = []
        for product in ranked_items[:max_recommendations]:
            category = product.get("category")
            base_color = base_item.get("color") or "사용자 스타일"
            base_category = base_item.get("category") or "아이템"
            base_mood = base_item.get("mood") or "무드"
            recommendations.append(
                {
                    "item_id": product.get("item_id"),
                    "source": product.get("source", "musinsa"),
                    "item_name": product.get("item_name") or product.get("name"),
                    "brand": product.get("brand"),
                    "category": category,
                    "image_url": product["image_url"],
                    "product_url": product.get("product_url"),
                    "price": product.get("price"),
                    "reason": f"{base_color} {base_category}의 {base_mood}와 잘 이어지는 {category or '아이템'}입니다.",
                }
            )

        return {
            "status": "success",
            "message": "사용자 요청과 스타일 정보를 바탕으로 어울리는 코디를 구성했습니다.",
            "recommendations": recommendations,
            "style_guide": {
                "summary": "오늘의 추천 코디" if retrieval_target == "hybrid" else "맞춤 추천 코디",
                "tips": [
                    "추천 리스트의 상품 안에서만 조합했습니다.",
                    "색상과 무드가 충돌하지 않도록 안정적인 아이템을 우선 배치했습니다.",
                ],
            },
        }


class DeterministicRagService(RagService):
    """검색을 실제로 실행하는 지점만 가로챈다."""

    async def _external_search_request(self, request: RAGRequest) -> dict:
        return await self._mock_search_request(request)

    async def _mock_search_request(self, request: RAGRequest) -> dict:
        excluded_item_refs = request.filters.get("excluded_item_refs") or []
        refresh_seed = int(request.filters.get("refresh_seed") or 0)

        if request.retrieval_target == "closet":
            items = self._search_closet(request, limit=request.top_k, refresh_seed=refresh_seed)
        elif request.retrieval_target == "musinsa":
            items = await self.search(
                request.query,
                limit=request.top_k,
                refresh_seed=refresh_seed,
                excluded_item_refs=excluded_item_refs,
            )
        else:
            closet_items = self._search_closet(
                request,
                limit=request.top_k,
                refresh_seed=refresh_seed,
            )
            catalog_items = await self.search(
                request.query,
                limit=request.top_k,
                refresh_seed=refresh_seed,
                excluded_item_refs=excluded_item_refs,
            )
            items = self._interleave(closet_items, catalog_items)[: request.top_k]

        return {
            "items": [self._normalize_item(item) for item in items],
            "message": "success" if items else "검색 결과가 없습니다.",
        }

    async def search(
        self,
        query: str,
        limit: int = 5,
        refresh_seed: int = 0,
        excluded_item_refs: list[str] | None = None,
    ) -> list[dict]:
        excluded_refs = {
            str(item_ref).strip()
            for item_ref in excluded_item_refs or []
            if str(item_ref).strip()
        }
        catalog = [
            item
            for item in self._mock_catalog()
            if not excluded_refs.intersection(
                str(item.get(key) or "").strip()
                for key in ("item_id", "product_url", "image_url")
                if str(item.get(key) or "").strip()
            )
        ]
        if not catalog or limit <= 0:
            return []

        start = (max(refresh_seed, 0) * limit) % len(catalog)
        return [catalog[(start + offset) % len(catalog)] for offset in range(min(limit, len(catalog)))]

    def _mock_catalog(self) -> list[dict]:
        # Temporary Musinsa-like catalog used until vector retrieval is wired.
        return [
            {
                "item_id": "6081171",
                "source": "musinsa",
                "item_name": "[SET] 그래픽 피그먼트 오버핏 반팔티셔츠 VOL.01",
                "brand": "모즈모즈",
                "category": "상의",
                "price": 54400,
                "image_url": "https://image.msscdn.net/images/goods_img/20260304/6081171/6081171_17738881269364_500.jpg",
                "product_url": "https://www.musinsa.com/products/6081171",
            },
            {
                "item_id": "6075610",
                "source": "musinsa",
                "item_name": "CRACKED STAR HALF T BEIGE",
                "brand": "메종미네드",
                "category": "상의",
                "price": 38700,
                "image_url": "https://image.msscdn.net/images/goods_img/20260303/6075610/6075610_17733148086242_500.jpg",
                "product_url": "https://www.musinsa.com/products/6075610",
            },
            {
                "item_id": "6103287",
                "source": "musinsa",
                "item_name": "와플 헨리넥 데일리 반팔 티셔츠 - 2color",
                "brand": "마인드브릿지",
                "category": "상의",
                "price": 29900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260309/6103287/6103287_17750284664520_500.jpg",
                "product_url": "https://www.musinsa.com/products/6103287",
            },
            {
                "item_id": "6125368",
                "source": "musinsa",
                "item_name": "[JURASSIC WORLD] Mosasaurus Tee_VTG Black",
                "brand": "파르티멘토 리웍스",
                "category": "상의",
                "price": 49900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260312/6125368/6125368_17744981268200_500.jpg",
                "product_url": "https://www.musinsa.com/products/6125368",
            },
            {
                "item_id": "6084669",
                "source": "musinsa",
                "item_name": "NYC LOCATION T-SHIRT (23COLOR) (LRAMCTR702P)",
                "brand": "그루브라임",
                "category": "상의",
                "price": 9900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260305/6084669/6084669_17726862036016_500.jpg",
                "product_url": "https://www.musinsa.com/products/6084669",
            },
            {
                "item_id": "6102395",
                "source": "musinsa",
                "item_name": "소프트 그래픽 쇼트 슬리브 티셔츠",
                "brand": "아웃스탠딩",
                "category": "상의",
                "price": 33000,
                "image_url": "https://image.msscdn.net/images/goods_img/20260309/6102395/6102395_17739872421263_500.jpg",
                "product_url": "https://www.musinsa.com/products/6102395",
            },
            {
                "item_id": "6129443",
                "source": "musinsa",
                "item_name": "코리안 레터링 반팔 티셔츠 - NAVY",
                "brand": "마크곤잘레스",
                "category": "상의",
                "price": 46550,
                "image_url": "https://image.msscdn.net/images/goods_img/20260313/6129443/6129443_17737134775461_500.jpg",
                "product_url": "https://www.musinsa.com/products/6129443",
            },
            {
                "item_id": "6086792",
                "source": "musinsa",
                "item_name": "릴랙스 그래픽 반팔 티셔츠",
                "brand": "스파오",
                "category": "상의",
                "price": 25900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260305/6086792/6086792_17727543246534_500.jpg",
                "product_url": "https://www.musinsa.com/products/6086792",
            },
            {
                "item_id": "6125389",
                "source": "musinsa",
                "item_name": "[BACK TO THE FUTURE] Duo Tee_VTG Black",
                "brand": "파르티멘토 리웍스",
                "category": "상의",
                "price": 49900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260312/6125389/6125389_17744979667151_500.jpg",
                "product_url": "https://www.musinsa.com/products/6125389",
            },
            {
                "item_id": "6108783",
                "source": "musinsa",
                "item_name": "Chaser 링거 반팔 티셔츠",
                "brand": "비바스튜디오",
                "category": "상의",
                "price": 35900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260310/6108783/6108783_17742516004008_500.jpg",
                "product_url": "https://www.musinsa.com/products/6108783",
            },
            {
                "item_id": "6107195",
                "source": "musinsa",
                "item_name": "PENGUIN CHARACTER T-SHIRTS (LRPMCTA419M)",
                "brand": "그루브라임",
                "category": "상의",
                "price": 8750,
                "image_url": "https://image.msscdn.net/images/goods_img/20260310/6107195/6107195_17731192461772_500.jpg",
                "product_url": "https://www.musinsa.com/products/6107195",
            },
            {
                "item_id": "6109669",
                "source": "musinsa",
                "item_name": "Youthful 반팔 티셔츠",
                "brand": "비바스튜디오",
                "category": "상의",
                "price": 28400,
                "image_url": "https://image.msscdn.net/images/goods_img/20260310/6109669/6109669_17731302889705_500.jpg",
                "product_url": "https://www.musinsa.com/products/6109669",
            },
        ]


class DeterministicVlmService(VlmService):
    """이미지를 실제로 분석하는 지점만 가로챈다."""

    async def _external_analyze_many(self, image_urls: list[str], multi_item: bool = True) -> dict:
        return await self._mock_analyze_many(image_urls)

    async def _mock_analyze_many(self, image_urls: list[str]) -> dict:
        items = [self._mock_item(image_url) for image_url in image_urls]
        response = {
            "items": items,
            "is_fashion_item": all(bool(item.get("is_fashion_item")) for item in items) if items else True,
        }
        return VLMResponse.model_validate(response).model_dump()

    def _mock_item(self, image_url: str) -> dict:
        # Mark obvious non-fashion test URLs as invalid fashion inputs.
        lowered = image_url.lower()
        is_fashion_item = not any(word in lowered for word in ["food", "cat", "dog", "car", "landscape"])
        return {
            "name": "업로드 의류 이미지",
            "brand": "unknown",
            "price": None,
            "category": "상의",
            "label": "니트",
            "gender": "unisex",
            "thumbnail_url": image_url,
            "product_url": None,
            "color": "white",
            "material": "cotton",
            "fit": "oversized",
            "pattern": "solid",
            "mood": "casual",
            "sense_of_season": "spring",
            "is_fashion_item": is_fashion_item,
        }
