import json
from typing import Any

import httpx

from app.core.config import settings
from app.schemas.recommendation import AgentResponse
from app.services.gemini_client import extract_text, parse_json_object


MAX_LLM_CANDIDATES = 8
MAX_RECOMMENDATIONS = 5


class LlmService:
    def __init__(self, use_mock_ai: bool | None = None) -> None:
        self.use_mock_ai = settings.use_mock_ai if use_mock_ai is None else use_mock_ai

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
                "thinkingConfig": {"thinkingLevel": "low"},
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
        return extract_text(response)

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        return parse_json_object(content)

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
