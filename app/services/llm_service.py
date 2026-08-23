import json
import re
from typing import Any

import httpx

from app.agent.prompts import (
    GENERAL_CHAT_SYSTEM_PROMPT,
    INTENT_CLASSIFIER_SYSTEM_PROMPT,
    QUERY_REFINER_SYSTEM_PROMPT,
    RETRIEVAL_PLANNER_SYSTEM_PROMPT,
    build_conversation_query,
    build_rag_query,
)
from app.core.config import settings
from app.schemas.ai import GeneralChatAnswer, IntentClassification, QueryRefinement, RetrievalPlan
from app.schemas.recommendation import AgentResponse


MAX_LLM_CANDIDATES = 8
MAX_RECOMMENDATIONS = 5
MAX_PREVIOUS_RAG_CANDIDATES = 20


class LlmService:
    def __init__(self, use_mock_ai: bool | None = None) -> None:
        self.use_mock_ai = settings.use_mock_ai if use_mock_ai is None else use_mock_ai

    async def classify_intent(
        self,
        query: str,
        chat_history: list[dict] | None = None,
        has_image: bool = False,
    ) -> dict[str, Any]:
        """Classify the turn before any potentially unnecessary VLM/RAG calls."""
        if self.use_mock_ai:
            result = self._mock_classify_intent(query, chat_history or [], has_image)
        else:
            result = await self._generate_structured(
                system_instruction=INTENT_CLASSIFIER_SYSTEM_PROMPT,
                prompt={
                    "current_query": query,
                    "chat_history": chat_history or [],
                    "has_attached_image": has_image,
                },
                response_schema=self._intent_schema(),
                temperature=0.0,
            )
        return IntentClassification.model_validate(result).model_dump()

    async def refine_query(
        self,
        query: str,
        chat_history: list[dict] | None = None,
        vlm_items: list[dict] | None = None,
    ) -> str:
        """Create the standalone query consumed by retrieval."""
        if self.use_mock_ai:
            result = self._mock_refine_query(query, chat_history or [], vlm_items or [])
        else:
            result = await self._generate_structured(
                system_instruction=QUERY_REFINER_SYSTEM_PROMPT,
                prompt={
                    "current_query": query,
                    "chat_history": chat_history or [],
                    "vlm_items": vlm_items or [],
                },
                response_schema=self._query_refinement_schema(),
                temperature=0.1,
            )
        return QueryRefinement.model_validate(result).query.strip()

    async def plan_retrieval(
        self,
        query: str,
        original_query: str,
        chat_history: list[dict] | None = None,
        previous_rag_results: list[dict] | None = None,
        previous_shown_item_refs: list[str] | None = None,
        previous_rag_query: str | None = None,
        previous_retrieval_target: str | None = None,
        closet_items: list[dict] | None = None,
        user_profile: dict | None = None,
        vlm_items: list[dict] | None = None,
        use_closet_style: bool = True,
    ) -> dict[str, Any]:
        """Let the LLM choose both the retrieval source and reuse/retrieve path."""
        shown_item_refs = {
            str(item_ref).strip()
            for item_ref in previous_shown_item_refs or []
            if str(item_ref).strip()
        }
        previous_items = self._planner_items(previous_rag_results or [], shown_item_refs)
        if self.use_mock_ai:
            result = self._mock_plan_retrieval(
                query=query,
                original_query=original_query,
                previous_items=previous_items,
                previous_retrieval_target=previous_retrieval_target,
                closet_items=closet_items or [],
                user_profile=user_profile or {},
                vlm_items=vlm_items or [],
                use_closet_style=use_closet_style,
            )
        else:
            result = await self._generate_structured(
                system_instruction=RETRIEVAL_PLANNER_SYSTEM_PROMPT,
                prompt={
                    "current_query": original_query,
                    "refined_query": query,
                    "chat_history": chat_history or [],
                    "previous_rag_query": previous_rag_query,
                    "previous_retrieval_target": previous_retrieval_target,
                    "previous_rag_items": previous_items,
                    "selected_closet_items": closet_items or [],
                    "user_profile": user_profile or {},
                    "vlm_items": vlm_items or [],
                    "use_closet_style": use_closet_style,
                },
                response_schema=self._retrieval_plan_schema(),
                temperature=0.0,
            )

        plan = RetrievalPlan.model_validate(result)
        available_refs = {
            item["ref"]
            for item in previous_items
            if plan.candidate_scope == "all"
            or (plan.candidate_scope == "shown" and item["was_shown"])
            or (plan.candidate_scope == "unseen" and not item["was_shown"])
        }
        selected_refs = list(
            dict.fromkeys(ref for ref in plan.selected_item_refs if ref in available_refs)
        )
        # Reuse is safe only when the model selected at least one supplied candidate.
        if plan.action == "reuse" and selected_refs:
            return plan.model_copy(update={"selected_item_refs": selected_refs}).model_dump()
        return plan.model_copy(update={"action": "retrieve", "selected_item_refs": []}).model_dump()

    async def compose_general_chat(
        self,
        query: str,
        chat_history: list[dict] | None = None,
    ) -> dict[str, Any]:
        if self.use_mock_ai:
            answer = self._mock_general_chat(query)
        else:
            answer = await self._generate_structured(
                system_instruction=GENERAL_CHAT_SYSTEM_PROMPT,
                prompt={"current_query": query, "chat_history": chat_history or []},
                response_schema=self._general_chat_schema(),
                temperature=0.5,
            )
        message = GeneralChatAnswer.model_validate(answer).message.strip()
        return AgentResponse(
            status="success",
            message=message,
            recommendations=[],
            style_guide=None,
        ).model_dump()

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
        # Always return the same public contract regardless of mock or real LLM.
        if self.use_mock_ai:
            response = await self._mock_compose_recommendation(query, vlm_items, ranked_items, retrieval_target)
        else:
            response = await self._external_compose_recommendation(
                query,
                vlm_items,
                ranked_items,
                retrieval_target,
                closet_items=closet_items or [],
                use_closet_style=use_closet_style,
                user_profile=user_profile or {},
                chat_history=chat_history or [],
            )
        return AgentResponse.model_validate(response).model_dump()

    async def _mock_compose_recommendation(
        self,
        query: str,
        vlm_items: list[dict],
        ranked_items: list[dict],
        retrieval_target: str = "musinsa",
    ) -> dict:
        if not ranked_items:
            return self._empty_response()

        base_item = vlm_items[0] if vlm_items else {}
        recommendations = []
        for product in ranked_items[:5]:
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
    ) -> dict:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        if not ranked_items:
            return self._empty_response()

        # Gemini receives only retrieved candidates so it cannot invent products.
        candidate_items = self._candidate_items(ranked_items)
        payload = self._build_gemini_payload(
            query,
            vlm_items,
            ranked_items,
            retrieval_target,
            closet_items=closet_items or [],
            use_closet_style=use_closet_style,
            user_profile=user_profile or {},
            chat_history=chat_history or [],
        )
        url = f"{settings.gemini_base_url.rstrip('/')}/models/{settings.gemini_model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": settings.gemini_api_key,
        }

        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()

        content = self._extract_gemini_text(response.json())
        parsed = self._parse_json_object(content)
        normalized = self._normalize_llm_response(parsed, candidate_items)
        return AgentResponse.model_validate(normalized).model_dump()

    async def _generate_structured(
        self,
        system_instruction: str,
        prompt: dict[str, Any],
        response_schema: dict[str, Any],
        temperature: float,
    ) -> dict[str, Any]:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction.strip()}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": json.dumps(prompt, ensure_ascii=False)}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseSchema": response_schema,
            },
        }
        url = f"{settings.gemini_base_url.rstrip('/')}/models/{settings.gemini_model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": settings.gemini_api_key,
        }
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        return self._parse_json_object(self._extract_gemini_text(response.json()))

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
    ) -> dict[str, str]:
        conversation_query = build_conversation_query(chat_history, query)
        refined = build_rag_query({"items": vlm_items}, conversation_query)
        return {"query": refined or str(query).strip()}

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
        """Deterministic mock used only when USE_MOCK_AI=true."""
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

    def _planner_items(
        self,
        items: list[dict],
        shown_item_refs: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        shown_refs = shown_item_refs or set()
        planned: list[dict[str, Any]] = []
        for item in items[:MAX_PREVIOUS_RAG_CANDIDATES]:
            if not isinstance(item, dict):
                continue
            item_ref = self._item_ref(item)
            if not item_ref:
                continue
            planned.append(
                {
                    "ref": item_ref,
                    "was_shown": item_ref in shown_refs,
                    "item_id": item.get("item_id"),
                    "source": item.get("source"),
                    "name": item.get("name") or item.get("item_name"),
                    "brand": item.get("brand"),
                    "category": item.get("category"),
                    "price": item.get("price"),
                    "color": item.get("color"),
                    "material": item.get("material"),
                    "fit": item.get("fit"),
                    "pattern": item.get("pattern"),
                    "mood": item.get("mood"),
                    "sense_of_season": item.get("sense_of_season"),
                }
            )
        return planned

    def _category_group(self, value: str) -> str | None:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None
        aliases = {
            "top": ("상의", "셔츠", "니트", "티셔츠", "top", "shirt", "knit"),
            "pants": ("하의", "바지", "팬츠", "데님", "슬랙스", "pants", "trouser", "jeans"),
            "skirt": ("치마", "스커트", "skirt"),
            "dress": ("원피스", "dress"),
            "outer": ("아우터", "재킷", "자켓", "코트", "점퍼", "outer", "jacket", "coat"),
            "shoes": ("신발", "슈즈", "스니커즈", "구두", "shoes", "sneaker"),
            "bag": ("가방", "백", "bag"),
            "hat": ("모자", "캡", "hat", "cap"),
        }
        for group, terms in aliases.items():
            if any(term in normalized for term in terms):
                return group
        return None

    def _item_ref(self, item: dict[str, Any]) -> str | None:
        for key in ("item_id", "product_url", "image_url"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        return None

    def _intent_schema(self) -> dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "intent": {"type": "STRING", "enum": ["general_chat", "fashion_service"]},
                "reason": {"type": "STRING", "nullable": True},
            },
            "required": ["intent"],
        }

    def _query_refinement_schema(self) -> dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING"}},
            "required": ["query"],
        }

    def _retrieval_plan_schema(self) -> dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["reuse", "retrieve"]},
                "retrieval_target": {
                    "type": "STRING",
                    "enum": ["closet", "musinsa", "hybrid"],
                },
                "candidate_scope": {
                    "type": "STRING",
                    "enum": ["all", "shown", "unseen"],
                },
                "selected_item_refs": {"type": "ARRAY", "items": {"type": "STRING"}},
                "reason": {"type": "STRING", "nullable": True},
            },
            "required": ["action", "retrieval_target", "candidate_scope", "selected_item_refs"],
        }

    def _general_chat_schema(self) -> dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {"message": {"type": "STRING"}},
            "required": ["message"],
        }

    def _empty_response(self) -> dict:
        return {
            "status": "empty",
            "message": "조건에 맞는 추천 상품을 찾지 못했습니다.",
            "recommendations": [],
            "style_guide": None,
        }

    def _build_gemini_payload(
        self,
        query: str,
        vlm_items: list[dict],
        ranked_items: list[dict],
        retrieval_target: str,
        closet_items: list[dict] | None = None,
        use_closet_style: bool = True,
        user_profile: dict | None = None,
        chat_history: list[dict] | None = None,
    ) -> dict[str, Any]:
        candidate_items = self._candidate_items(ranked_items)
        prompt = {
            "user_query": query,
            # 시간순 이전 대화. "더 저렴한 걸로" 같은 후속 질문을 이해하는 데 쓴다.
            "chat_history": chat_history or [],
            "retrieval_target": retrieval_target,
            "vlm_items": vlm_items,
            "closet_items": closet_items or [],
            "use_closet_style": use_closet_style,
            "user_profile": user_profile or {},
            "candidate_items": candidate_items,
            "response_contract": {
                "status": "success | empty | error",
                "message": "Korean user-facing summary",
                "recommendations": [
                    {
                        "item_id": "string or null",
                        "source": "closet or musinsa",
                        "item_name": "string or null",
                        "brand": "string or null",
                        "category": "string or null",
                        "image_url": "string",
                        "product_url": "required when source is musinsa",
                        "price": "integer or null",
                        "reason": "Korean reason based only on candidate_items",
                    }
                ],
                "style_guide": {
                    "summary": "Korean short outfit concept",
                    "tips": ["Korean styling tip"],
                },
            },
        }
        system_instruction = (
            "You are AID-FIT's fashion recommendation writer. "
            "Return only one valid JSON object matching the provided response_contract. "
            "Do not use markdown. Recommend only products present in candidate_items. "
            "Do not invent product names, brands, prices, image URLs, or product URLs. "
            "Use closet_items, use_closet_style, user_profile, and vlm_items only as styling context. "
            "Write all user-facing text in natural Korean."
        )
        return {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": json.dumps(prompt, ensure_ascii=False)}],
                }
            ],
            "generationConfig": {
                "temperature": 0.4,
                "responseMimeType": "application/json",
                "responseSchema": self._response_schema(),
            },
        }

    def _candidate_items(self, ranked_items: list[dict]) -> list[dict[str, Any]]:
        # Limit prompt size while preserving the highest-ranked candidates.
        return [
            {
                "item_id": item.get("item_id"),
                "source": item.get("source", "musinsa"),
                "item_name": item.get("item_name") or item.get("name"),
                "brand": item.get("brand"),
                "category": item.get("category"),
                "image_url": item.get("image_url"),
                "product_url": item.get("product_url"),
                "price": item.get("price"),
                "color": item.get("color"),
                "material": item.get("material"),
                "fit": item.get("fit"),
                "pattern": item.get("pattern"),
                "mood": item.get("mood"),
                "sense_of_season": item.get("sense_of_season"),
            }
            for item in ranked_items[:MAX_LLM_CANDIDATES]
            if item.get("image_url")
        ]

    def _response_schema(self) -> dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "status": {"type": "STRING", "enum": ["success", "empty", "error"]},
                "message": {"type": "STRING"},
                "recommendations": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "item_id": {"type": "STRING", "nullable": True},
                            "source": {"type": "STRING", "enum": ["closet", "musinsa"]},
                            "item_name": {"type": "STRING", "nullable": True},
                            "brand": {"type": "STRING", "nullable": True},
                            "category": {"type": "STRING", "nullable": True},
                            "image_url": {"type": "STRING"},
                            "product_url": {"type": "STRING", "nullable": True},
                            "price": {"type": "INTEGER", "nullable": True},
                            "reason": {"type": "STRING"},
                        },
                        "required": ["source", "image_url", "reason"],
                    },
                },
                "style_guide": {
                    "type": "OBJECT",
                    "nullable": True,
                    "properties": {
                        "summary": {"type": "STRING"},
                        "tips": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                    "required": ["summary", "tips"],
                },
            },
            "required": ["status", "message", "recommendations", "style_guide"],
        }

    def _extract_gemini_text(self, response: dict[str, Any]) -> str:
        # Gemini wraps generated text inside candidate content parts.
        candidates = response.get("candidates") or []
        if not candidates:
            raise RuntimeError("Gemini returned no candidates")

        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        text_parts = [part.get("text", "") for part in parts if part.get("text")]
        content = "".join(text_parts).strip()
        if not content:
            raise RuntimeError("Gemini returned empty content")
        return content

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Be tolerant of providers that wrap JSON with extra text.
            match = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if not match:
                raise
            parsed = json.loads(match.group(0))

        if not isinstance(parsed, dict):
            raise ValueError("Gemini response must be a JSON object")
        return parsed

    def _normalize_llm_response(self, response: dict[str, Any], candidate_items: list[dict[str, Any]]) -> dict[str, Any]:
        candidates_by_id = {
            str(item["item_id"]): item for item in candidate_items if item.get("item_id") is not None
        }
        candidates_by_url = {item["image_url"]: item for item in candidate_items if item.get("image_url")}

        normalized_recommendations = []
        for recommendation in response.get("recommendations") or []:
            if not isinstance(recommendation, dict):
                continue

            candidate = None
            item_id = recommendation.get("item_id")
            if item_id is not None:
                candidate = candidates_by_id.get(str(item_id))
            if candidate is None and recommendation.get("image_url"):
                candidate = candidates_by_url.get(recommendation["image_url"])
            if candidate is None:
                continue

            normalized_recommendations.append(
                {
                    "item_id": candidate.get("item_id"),
                    "source": candidate.get("source", "musinsa"),
                    "item_name": candidate.get("item_name"),
                    "brand": candidate.get("brand"),
                    "category": candidate.get("category"),
                    "image_url": candidate["image_url"],
                    "product_url": candidate.get("product_url"),
                    "price": candidate.get("price"),
                    "reason": str(recommendation.get("reason") or "").strip()
                    or "사용자 요청과 잘 맞는 추천 상품입니다.",
                }
            )
            if len(normalized_recommendations) >= MAX_RECOMMENDATIONS:
                break

        if not normalized_recommendations:
            return self._empty_response()

        style_guide = response.get("style_guide")
        if not isinstance(style_guide, dict) or not style_guide.get("summary"):
            style_guide = {"summary": "추천 상품 기반 코디", "tips": []}

        return {
            "status": "success",
            "message": str(response.get("message") or "추천 결과를 생성했습니다."),
            "recommendations": normalized_recommendations,
            "style_guide": {
                "summary": str(style_guide.get("summary")),
                "tips": [str(tip) for tip in style_guide.get("tips", []) if tip],
            },
        }
