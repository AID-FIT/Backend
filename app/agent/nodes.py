from typing import Any

from pydantic import ValidationError

from app.agent.prompts import build_rag_query
from app.agent.state import AgentState
from app.schemas.ai import AgentError, RAGRequest, RAGResponse, VLMRequest, VLMResponse
from app.schemas.recommendation import AgentResponse
from app.services.llm_service import LlmService
from app.services.rag_service import RagService
from app.services.vlm_service import VlmService


def build_error(code: str, message: str, retryable: bool, source: str) -> dict[str, Any]:
    # Validate internal errors before they are attached to graph state.
    return AgentError(code=code, message=message, retryable=retryable, source=source).model_dump()


def _term(value: Any) -> str:
    return str(value or "").strip().lower()


def _terms_from_item(item: dict[str, Any], keys: tuple[str, ...]) -> set[str]:
    # Normalize metadata values once so ranking comparisons stay simple.
    return {_term(item.get(key)) for key in keys if _term(item.get(key))}


def _has_profile_context(user_profile: dict[str, Any]) -> bool:
    return any(bool(value) for value in user_profile.values())


def _has_vlm_closet_signal(vlm_items: list[dict[str, Any]]) -> bool:
    for item in vlm_items:
        source = _term(item.get("source"))
        item_id = _term(item.get("item_id") or item.get("closet_item_id"))
        product_url = _term(item.get("product_url"))
        if source == "closet" or item_id.startswith("closet") or product_url.startswith("closet://"):
            return True
    return False


class AgentNodes:
    def __init__(
        self,
        vlm_service: VlmService | None = None,
        rag_service: RagService | None = None,
        llm_service: LlmService | None = None,
    ) -> None:
        self.vlm_service = vlm_service or VlmService()
        self.rag_service = rag_service or RagService()
        self.llm_service = llm_service or LlmService()

    async def input_validation_node(self, state: AgentState) -> AgentState:
        # Fail fast before calling any external AI service.
        if not str(state.get("query") or "").strip():
            state["error"] = build_error("INVALID_INPUT", "사용자 요청(query)이 비어 있습니다.", False, "agent")
            return state

        if not isinstance(state.get("image_urls", []), list):
            state["error"] = build_error("INVALID_INPUT", "image_urls 형식이 올바르지 않습니다.", False, "agent")
            return state

        closet_items = state.get("closet_items", [])
        if not isinstance(closet_items, list):
            state["error"] = build_error("INVALID_CLOSET_ITEMS", "옷장 아이템 형식이 올바르지 않습니다.", False, "agent")
            return state

        for item in closet_items:
            if not isinstance(item, dict):
                state["error"] = build_error(
                    "INVALID_CLOSET_ITEMS",
                    "옷장 아이템 형식이 올바르지 않습니다.",
                    False,
                    "agent",
                )
                return state
            if not item.get("closet_item_id"):
                state["error"] = build_error(
                    "MISSING_CLOSET_ITEM_ID",
                    "선택한 옷장 아이템의 ID가 누락되었습니다.",
                    False,
                    "agent",
                )
                return state

        return state

    async def context_check_node(self, state: AgentState) -> AgentState:
        # These flags decide whether the graph needs VLM and closet-aware routing.
        state["has_image"] = len(state.get("image_urls") or []) > 0
        state["has_closet_items"] = len(state.get("closet_items") or []) > 0
        return state

    async def vlm_node(self, state: AgentState) -> AgentState:
        try:
            vlm_response = await self.call_vlm(state.get("image_urls") or [])
        except ValidationError:
            state["error"] = build_error("VLM_INVALID_RESPONSE", "이미지 분석 결과 형식이 올바르지 않습니다.", True, "vlm")
            return state
        except Exception:
            state["error"] = build_error("VLM_ANALYSIS_FAILED", "이미지 분석에 실패했습니다. 다시 시도해주세요.", True, "vlm")
            return state

        state["vlm_items"] = vlm_response.get("items", [])
        state["vlm_result"] = vlm_response
        state["is_fashion_item"] = bool(vlm_response.get("is_fashion_item", True))
        return state

    async def call_vlm(self, image_urls: list[str]) -> dict[str, Any]:
        # Contract validation keeps the graph independent from VLM implementation details.
        request = VLMRequest(image_urls=image_urls)
        response = await self.vlm_service.analyze_many(request.image_urls)
        return VLMResponse.model_validate(response).model_dump(by_alias=False)

    async def fashion_item_check_node(self, state: AgentState) -> AgentState:
        if state.get("error"):
            return state
        if state.get("has_image") and not state.get("is_fashion_item", False):
            state["error"] = build_error(
                "VLM_NOT_FASHION_ITEM",
                "의류 아이템이 명확하게 보이는 이미지를 업로드해주세요.",
                True,
                "vlm",
            )
        return state

    async def intent_classifier_node(self, state: AgentState) -> AgentState:
        query = _term(state.get("query"))
        has_closet_items = bool(state.get("has_closet_items") or state.get("closet_items"))
        use_closet_style = bool(state.get("use_closet_style", True))
        user_profile = state.get("user_profile") or {}
        vlm_items = state.get("vlm_items") or []
        has_profile_context = _has_profile_context(user_profile)
        has_vlm_closet_signal = _has_vlm_closet_signal(vlm_items)

        # Explicit user intent wins before style/profile-based defaults.
        closet_only_keywords = [
            "내 옷장",
            "내옷장",
            "옷장 안에서",
            "옷장 안",
            "내 옷으로",
            "내옷으로",
            "가진 옷",
            "가지고 있는 옷",
            "closet",
        ]
        musinsa_keywords = ["무신사", "구매", "살 만한", "살만한", "상품", "사고 싶은", "buy", "musinsa"]

        state["intent"] = "style_recommendation"
        if any(keyword in query for keyword in closet_only_keywords):
            state["retrieval_target"] = "closet"
        elif any(keyword in query for keyword in musinsa_keywords):
            state["retrieval_target"] = "musinsa"
        elif has_closet_items:
            state["retrieval_target"] = "hybrid"
        elif has_vlm_closet_signal:
            state["retrieval_target"] = "hybrid"
        elif use_closet_style and has_profile_context:
            state["retrieval_target"] = "hybrid"
        else:
            state["retrieval_target"] = "musinsa"
        return state

    async def build_rag_request_node(self, state: AgentState) -> AgentState:
        # Combine user text, VLM metadata, profile, and context into one RAG contract.
        context = state.get("context") or {}
        vlm_items = state.get("vlm_items") or []
        user_profile = state.get("user_profile") or {}
        query = build_rag_query({"items": vlm_items}, state["query"])
        filters = self._build_rag_filters(context, user_profile, vlm_items)

        request = RAGRequest(
            user_id=state["user_id"],
            query=query,
            retrieval_target=state.get("retrieval_target", "musinsa"),
            user_profile=user_profile,
            vlm_items=vlm_items,
            closet_items=state.get("closet_items") or [],
            use_closet_style=state.get("use_closet_style", True),
            filters=filters,
            top_k=int(context.get("limit") or context.get("top_k") or 10),
        )
        state["rag_query"] = query
        state["rag_request"] = request.model_dump()
        return state

    def _build_rag_filters(self, context: dict[str, Any], user_profile: dict[str, Any], vlm_items: list[dict]) -> dict:
        # Explicit context filters win over profile or image-inferred defaults.
        filters = {
            "refresh_seed": context.get("refresh_seed", 0),
        }
        for key in (
            "price_min",
            "price_max",
            "season",
            "style",
            "preferred_styles",
            "sense_of_season",
            "category",
            "color",
            "gender",
        ):
            if key in context:
                filters[key] = context[key]

        preferred_styles = user_profile.get("preferred_styles") or []
        if preferred_styles and "preferred_styles" not in filters:
            filters["preferred_styles"] = preferred_styles

        for key, value in self._inferred_vlm_filters(vlm_items).items():
            if value and key not in filters:
                filters[key] = value
        return filters

    def _inferred_vlm_filters(self, vlm_items: list[dict]) -> dict[str, Any]:
        # An outfit photo carries several garments, so no single one may narrow the search.
        if len(vlm_items) > 1:
            return {"sense_of_season": self._unanimous_value(vlm_items, "sense_of_season")}

        first_vlm_item = vlm_items[0] if vlm_items else {}
        return {
            "sense_of_season": first_vlm_item.get("sense_of_season"),
            "category": first_vlm_item.get("category"),
            "color": first_vlm_item.get("color"),
        }

    def _unanimous_value(self, vlm_items: list[dict], key: str) -> Any:
        values = {_term(item.get(key)) for item in vlm_items}
        values.discard("")
        return values.pop() if len(values) == 1 else None

    async def closet_rag_node(self, state: AgentState) -> AgentState:
        return await self._run_rag(state, "closet")

    async def musinsa_rag_node(self, state: AgentState) -> AgentState:
        return await self._run_rag(state, "musinsa")

    async def hybrid_rag_node(self, state: AgentState) -> AgentState:
        return await self._run_rag(state, "hybrid")

    async def _run_rag(self, state: AgentState, retrieval_target: str) -> AgentState:
        rag_request = {**state["rag_request"], "retrieval_target": retrieval_target}
        try:
            rag_response = await self.call_rag(rag_request)
        except ValidationError:
            state["error"] = build_error("RAG_INVALID_RESPONSE", "추천 상품 검색 결과 형식이 올바르지 않습니다.", True, "rag")
            return state
        except Exception:
            state["error"] = build_error("RAG_SEARCH_FAILED", "추천 상품 검색 중 오류가 발생했습니다.", True, "rag")
            return state

        state["rag_request"] = rag_request
        state["rag_results"] = rag_response.get("items", [])
        state["rag_items"] = state["rag_results"]
        return state

    async def call_rag(self, rag_request: dict[str, Any]) -> dict[str, Any]:
        # Validate both sides of the RAG boundary before continuing the graph.
        request = RAGRequest.model_validate(rag_request)
        response = await self.rag_service.search_request(request.model_dump())
        return RAGResponse.model_validate(response).model_dump()

    async def rag_result_check_node(self, state: AgentState) -> AgentState:
        state["has_rag_result"] = bool(state.get("rag_results"))
        return state

    async def fallback_search_node(self, state: AgentState) -> AgentState:
        fallback_count = int(state.get("fallback_count") or 0)
        if fallback_count >= 1:
            state["has_rag_result"] = bool(state.get("rag_results"))
            return state

        # Match the interface spec: relax style/season and widen the result set.
        filters = dict((state.get("rag_request") or {}).get("filters") or {})
        for key in ("style", "season"):
            filters.pop(key, None)

        rag_request = {
            **(state.get("rag_request") or {}),
            "filters": filters,
            "top_k": max(int((state.get("rag_request") or {}).get("top_k") or 10), 20),
        }

        try:
            rag_response = await self.call_rag(rag_request)
        except ValidationError:
            state["error"] = build_error("RAG_INVALID_RESPONSE", "추천 상품 검색 결과 형식이 올바르지 않습니다.", True, "rag")
            return state
        except Exception:
            state["error"] = build_error("RAG_SEARCH_FAILED", "추천 상품 검색 중 오류가 발생했습니다.", True, "rag")
            return state

        state["fallback_count"] = fallback_count + 1
        state["rag_request"] = rag_request
        state["rag_results"] = rag_response.get("items", [])
        state["rag_items"] = state["rag_results"]
        state["has_rag_result"] = bool(state["rag_results"])
        return state

    async def style_ranker_node(self, state: AgentState) -> AgentState:
        state["ranked_items"] = sorted(
            state.get("rag_results", []),
            key=lambda item: self._ranking_score(item, state),
            reverse=True,
        )
        return state

    def _ranking_score(self, item: dict[str, Any], state: AgentState | None = None) -> float:
        score = self._base_ranking_score(item)
        if state is None:
            return score

        # Closet-style mode favors user taste and owned-item compatibility.
        if state.get("use_closet_style", True):
            score += self._preferred_style_bonus(item, state.get("user_profile") or {})
            score += self._closet_metadata_bonus(item, state.get("closet_items") or [])
        else:
            score += self._query_relevance_bonus(item, state.get("query", ""))
        return score

    def _base_ranking_score(self, item: dict[str, Any]) -> float:
        for key in ("final_score", "metadata_score", "similarity_score"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return 0.0

    def _preferred_style_bonus(self, item: dict[str, Any], user_profile: dict[str, Any]) -> float:
        preferred_styles = {_term(style) for style in user_profile.get("preferred_styles", []) if _term(style)}
        if not preferred_styles:
            return 0.0
        item_terms = _terms_from_item(item, ("mood", "pattern", "category", "label"))
        return 0.15 * len(preferred_styles & item_terms)

    def _closet_metadata_bonus(self, item: dict[str, Any], closet_items: list[dict[str, Any]]) -> float:
        item_terms = _terms_from_item(
            item,
            ("color", "category", "mood", "sense_of_season", "pattern", "material", "fit"),
        )
        if not item_terms:
            return 0.0

        matches = 0
        for closet_item in closet_items:
            closet_terms = _terms_from_item(
                closet_item,
                ("color", "category", "mood", "sense_of_season", "pattern", "material", "fit"),
            )
            matches += len(item_terms & closet_terms)
        return min(matches * 0.05, 0.30)

    def _query_relevance_bonus(self, item: dict[str, Any], query: str) -> float:
        query_text = _term(query)
        if not query_text:
            return 0.0
        item_terms = _terms_from_item(
            item,
            ("color", "category", "mood", "sense_of_season", "pattern", "material", "fit", "label"),
        )
        matches = sum(1 for term in item_terms if term and term in query_text)
        return min(matches * 0.03, 0.12)

    async def final_response_node(self, state: AgentState) -> AgentState:
        try:
            # The LLM still has to satisfy the public backend response contract.
            response = await self.llm_service.compose_recommendation(
                state["query"],
                state.get("vlm_items", []),
                state.get("ranked_items", []),
                state.get("retrieval_target", "musinsa"),
                closet_items=state.get("closet_items", []),
                use_closet_style=state.get("use_closet_style", True),
                user_profile=state.get("user_profile", {}),
            )
            state["final_response"] = AgentResponse.model_validate(response).model_dump()
        except ValidationError:
            state["error"] = build_error("FINAL_RESPONSE_INVALID", "최종 추천 결과 형식이 올바르지 않습니다.", True, "llm")
            return await self.error_response_node(state)
        except Exception:
            state["error"] = build_error("FINAL_RESPONSE_FAILED", "최종 추천 결과 생성에 실패했습니다.", True, "llm")
            return await self.error_response_node(state)
        return state

    async def error_response_node(self, state: AgentState) -> AgentState:
        error = state.get("error") or build_error(
            "FINAL_RESPONSE_FAILED",
            "최종 추천 결과 생성에 실패했습니다.",
            True,
            "agent",
        )
        state["final_response"] = {
            "status": "error",
            "message": error["message"],
            "recommendations": [],
            "style_guide": None,
        }
        return state
