import json
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
from app.services.gemini_client import extract_text, parse_json_object


MAX_LLM_CANDIDATES = 8
MAX_RECOMMENDATIONS = 5
MAX_PREVIOUS_RAG_CANDIDATES = 20


def empty_recommendation_response() -> dict[str, Any]:
    """Return the shared public response used when retrieval found no candidates."""
    return {
        "status": "empty",
        "message": "조건에 맞는 추천 상품을 찾지 못했습니다.",
        "recommendations": [],
        "style_guide": None,
    }


class LlmService:
    async def classify_intent(
        self,
        query: str,
        chat_history: list[dict] | None = None,
        has_image: bool = False,
    ) -> dict[str, Any]:
        """Classify the turn before any potentially unnecessary VLM/RAG calls."""
        result = await self._generate_structured(
            system_instruction=INTENT_CLASSIFIER_SYSTEM_PROMPT,
            prompt={
                "current_query": query,
                "chat_history": chat_history or [],
                "has_attached_image": has_image,
            },
            response_schema=self._intent_schema(),
            temperature=0.0,
            model=settings.fast_model_name,
            thinking_budget=settings.llm_fast_thinking_budget,
        )
        return IntentClassification.model_validate(result).model_dump()

    async def refine_query(
        self,
        query: str,
        chat_history: list[dict] | None = None,
        vlm_items: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Create the structured query intent consumed by retrieval."""
        result = await self._generate_structured(
            system_instruction=QUERY_REFINER_SYSTEM_PROMPT,
            prompt={
                "current_query": query,
                "chat_history": chat_history or [],
                "vlm_items": vlm_items or [],
            },
            response_schema=self._query_refinement_schema(),
            temperature=0.1,
            model=settings.fast_model_name,
            thinking_budget=settings.llm_fast_thinking_budget,
        )
        return QueryRefinement.model_validate(result).model_dump()

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
            model=settings.fast_model_name,
            thinking_budget=settings.llm_fast_thinking_budget,
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
        max_recommendations: int | None = None,
    ) -> dict:
        # 홈 타일처럼 더 많은 카드를 채워야 하는 화면은 상한을 올려 잡는다.
        limit = max(1, max_recommendations or MAX_RECOMMENDATIONS)
        response = await self._external_compose_recommendation(
            query,
            vlm_items,
            ranked_items,
            retrieval_target,
            closet_items=closet_items or [],
            use_closet_style=use_closet_style,
            user_profile=user_profile or {},
            chat_history=chat_history or [],
            max_recommendations=limit,
        )
        return AgentResponse.model_validate(response).model_dump()

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
        max_recommendations: int = MAX_RECOMMENDATIONS,
    ) -> dict:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        if not ranked_items:
            return self._empty_response()

        # Gemini receives only retrieved candidates so it cannot invent products.
        # 뽑을 개수만큼만 보여주면 LLM이 고를 여지가 없다. 넉넉히 준다.
        candidate_limit = max(MAX_LLM_CANDIDATES, max_recommendations * 2)
        candidate_items = self._candidate_items(ranked_items, candidate_limit)
        payload = self._build_gemini_payload(
            query,
            vlm_items,
            ranked_items,
            retrieval_target,
            closet_items=closet_items or [],
            use_closet_style=use_closet_style,
            user_profile=user_profile or {},
            chat_history=chat_history or [],
            max_recommendations=max_recommendations,
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
        normalized = self._normalize_llm_response(parsed, candidate_items, max_recommendations)
        return AgentResponse.model_validate(normalized).model_dump()

    async def _generate_structured(
        self,
        system_instruction: str,
        prompt: dict[str, Any],
        response_schema: dict[str, Any],
        temperature: float,
        model: str | None = None,
        thinking_budget: int | None = None,
    ) -> dict[str, Any]:
        """구조화 응답을 받는 공통 경로.

        model과 thinking_budget은 단계마다 다르다. 분류·재작성처럼 추론이 필요 없는
        호출은 가벼운 모델에 추론을 끄고 돌린다. 둘 다 주지 않으면 기존 동작 그대로다.
        """
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
        }
        if thinking_budget is not None:
            generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction.strip()}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": json.dumps(prompt, ensure_ascii=False)}],
                }
            ],
            "generationConfig": generation_config,
        }
        url = (
            f"{settings.gemini_base_url.rstrip('/')}"
            f"/models/{model or settings.gemini_model}:generateContent"
        )
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": settings.gemini_api_key,
        }
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        return self._parse_json_object(self._extract_gemini_text(response.json()))

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
            "properties": {
                "query": {"type": "STRING"},
                "request_mode": {
                    "type": "STRING",
                    "enum": ["direct", "coordination", "similarity"],
                },
                "target_category": {
                    "type": "STRING",
                    "enum": ["상의", "바지", "아우터", "신발", "가방", "모자", "원피스/스커트"],
                    "nullable": True,
                },
            },
            "required": ["query", "request_mode", "target_category"],
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
        return empty_recommendation_response()

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
        max_recommendations: int = MAX_RECOMMENDATIONS,
    ) -> dict[str, Any]:
        candidate_limit = max(MAX_LLM_CANDIDATES, max_recommendations * 2)
        candidate_items = self._candidate_items(ranked_items, candidate_limit)
        prompt = {
            "user_query": query,
            # 목표 개수를 알려주지 않으면 모델이 두어 개만 고르고 끝낸다.
            "target_recommendation_count": max_recommendations,
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
            "Return exactly target_recommendation_count recommendations when candidate_items holds "
            "at least that many suitable products; return fewer only when it does not. "
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

    def _candidate_items(
        self, ranked_items: list[dict], limit: int = MAX_LLM_CANDIDATES
    ) -> list[dict[str, Any]]:
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
            for item in ranked_items[:limit]
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
        return extract_text(response)

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        return parse_json_object(content)

    def _normalize_llm_response(
        self,
        response: dict[str, Any],
        candidate_items: list[dict[str, Any]],
        max_recommendations: int = MAX_RECOMMENDATIONS,
    ) -> dict[str, Any]:
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
            if len(normalized_recommendations) >= max_recommendations:
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
