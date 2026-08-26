import logging
from typing import Any

from pydantic import ValidationError

from app.agent.state import AgentState, ChatHistoryMessage
from app.schemas.ai import (
    DEFAULT_RAG_TOP_K,
    AgentError,
    IntentClassification,
    QueryRefinement,
    RAGRequest,
    RAGResponse,
    RetrievalPlan,
    VLMRequest,
    VLMResponse,
)
from app.schemas.recommendation import AgentResponse
from app.services.catalog_matching import (
    infer_query_intents,
    is_vague_search_request,
    normalize_category,
    season_set,
    split_tokens,
)
from app.services.llm_service import LlmService
from app.services.rag_service import RagService
from app.services.style_embedding_service import (
    StyleEmbeddingService,
    cosine_similarity,
    get_style_embedding_service,
)
from app.services.target_category import query_names_a_category
from app.services.vlm_service import VlmService


logger = logging.getLogger(__name__)

# 개인화는 이 단계에서 한 번만 적용하며 각 신호의 최대 영향력을 고정한다.
PREFERRED_STYLE_MAX_BONUS = 0.15
CLOSET_PERSONALIZATION_MAX_BONUS = 0.20
REFERENCE_COMPATIBILITY_MAX_BONUS = 0.20
LIKED_ITEM_SIMILARITY_MAX_BONUS = 0.20
STYLE_PROFILE_DEPTH = 3
LIKED_ITEM_SEMANTIC_WEIGHT = 0.70
LIKED_ITEM_STRUCTURED_WEIGHT = 0.30
CLOSET_TASTE_WEIGHT = 0.55
CLOSET_COMPATIBILITY_WEIGHT = 0.45
COORDINATION_CLOSET_TASTE_WEIGHT = 0.25
COORDINATION_CLOSET_COMPATIBILITY_WEIGHT = 0.75
OUTFIT_SEMANTIC_WEIGHT = 0.60
OUTFIT_STRUCTURED_WEIGHT = 0.40

# Same-category items describe taste, while these cross-category pairs describe
# whether a new item can take part in an outfit with something already owned.
COMPLEMENTARY_CATEGORIES = {
    "상의": {"바지", "원피스/스커트", "아우터", "신발", "가방", "모자"},
    "바지": {"상의", "아우터", "신발", "가방", "모자"},
    "원피스/스커트": {"상의", "아우터", "신발", "가방", "모자"},
    "아우터": {"상의", "바지", "원피스/스커트", "신발", "가방", "모자"},
    "신발": {"상의", "바지", "원피스/스커트", "아우터", "가방", "모자"},
    "가방": {"상의", "바지", "원피스/스커트", "아우터", "신발", "모자"},
    "모자": {"상의", "바지", "원피스/스커트", "아우터", "신발", "가방"},
}
NEUTRAL_COLORS = {
    "black",
    "white",
    "gray",
    "charcoal",
    "ivory",
    "beige",
    "navy",
    "brown",
}
COLOR_HARMONY_PAIRS = {
    frozenset(("blue", "white")),
    frozenset(("blue", "beige")),
    frozenset(("navy", "white")),
    frozenset(("brown", "beige")),
    frozenset(("khaki", "white")),
    frozenset(("olive", "ivory")),
    frozenset(("red", "navy")),
    frozenset(("pink", "gray")),
}
ADJACENT_SEASON_PAIRS = {
    frozenset(("spring", "summer")),
    frozenset(("spring", "fall")),
    frozenset(("fall", "winter")),
}


def build_error(code: str, message: str, retryable: bool, source: str) -> dict[str, Any]:
    # Validate internal errors before they are attached to graph state.
    return AgentError(code=code, message=message, retryable=retryable, source=source).model_dump()


def _term(value: Any) -> str:
    return str(value or "").strip().lower()


def _terms_from_item(item: dict[str, Any], keys: tuple[str, ...]) -> set[str]:
    # Normalize metadata values once so ranking comparisons stay simple.
    terms: set[str] = set()
    for key in keys:
        terms.update(split_tokens(item.get(key)))
    return terms


def _item_ref(item: dict[str, Any]) -> str | None:
    for key in ("item_id", "product_url", "image_url"):
        item_ref = str(item.get(key) or "").strip()
        if item_ref:
            return item_ref
    return None


def _normalize_chat_history(value: Any) -> list[ChatHistoryMessage] | None:
    if not isinstance(value, list):
        return None

    normalized: list[ChatHistoryMessage] = []
    for message in value:
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            return None
        normalized.append({"role": role, "content": content})
    return normalized


class AgentNodes:
    def __init__(
        self,
        vlm_service: VlmService | None = None,
        rag_service: RagService | None = None,
        llm_service: LlmService | None = None,
        style_embedding_service: StyleEmbeddingService | None = None,
    ) -> None:
        self.vlm_service = vlm_service or VlmService()
        self.rag_service = rag_service or RagService()
        self.llm_service = llm_service or LlmService()
        self.style_embedding_service = (
            style_embedding_service or get_style_embedding_service()
        )

    async def input_validation_node(self, state: AgentState) -> AgentState:
        # Fail fast before calling any external AI service.
        if not str(state.get("query") or "").strip():
            state["error"] = build_error("INVALID_INPUT", "사용자 요청(query)이 비어 있습니다.", False, "agent")
            return state

        chat_history = _normalize_chat_history(state.get("chat_history", []))
        if chat_history is None:
            state["error"] = build_error("INVALID_CHAT_HISTORY", "대화 내역 형식이 올바르지 않습니다.", False, "agent")
            return state
        state["chat_history"] = chat_history
        state["resolved_query"] = str(state["query"]).strip()

        previous_rag_results = state.get("previous_rag_results", [])
        if not isinstance(previous_rag_results, list):
            # Previous context is an optimization, so stale data must not fail a new turn.
            previous_rag_results = []
        state["previous_rag_results"] = [
            item for item in previous_rag_results if isinstance(item, dict)
        ]
        state["candidate_pool"] = list(state["previous_rag_results"])
        previous_shown_item_refs = state.get("previous_shown_item_refs", [])
        if not isinstance(previous_shown_item_refs, list):
            previous_shown_item_refs = []
        state["previous_shown_item_refs"] = list(
            dict.fromkeys(
                str(item_ref).strip()
                for item_ref in previous_shown_item_refs
                if str(item_ref).strip()
            )
        )
        if state.get("previous_retrieval_target") not in {"closet", "musinsa", "hybrid"}:
            state["previous_retrieval_target"] = None

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

        liked_items = state.get("liked_items", [])
        if not isinstance(liked_items, list) or any(
            not isinstance(item, dict) for item in liked_items
        ):
            state["error"] = build_error(
                "INVALID_LIKED_ITEMS",
                "좋아요 상품 형식이 올바르지 않습니다.",
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
            # 트레이스백 없이 문구만 바꾸면 배포 후 원인을 볼 방법이 없다.
            logger.exception("agent node failed: %s", "VLM_INVALID_RESPONSE")
            state["error"] = build_error("VLM_INVALID_RESPONSE", "이미지 분석 결과 형식이 올바르지 않습니다.", True, "vlm")
            return state
        except Exception:
            # 트레이스백 없이 문구만 바꾸면 배포 후 원인을 볼 방법이 없다.
            logger.exception("agent node failed: %s", "VLM_ANALYSIS_FAILED")
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
        try:
            decision = await self.llm_service.classify_intent(
                query=state["query"],
                chat_history=state.get("chat_history", []),
                has_image=bool(state.get("has_image")),
            )
            classification = IntentClassification.model_validate(decision)
            state["intent"] = classification.intent
            state["intent_reason"] = classification.reason
        except ValidationError:
            state["error"] = build_error(
                "INTENT_INVALID_RESPONSE",
                "요청 분류 결과 형식이 올바르지 않습니다.",
                True,
                "llm",
            )
        except Exception:
            state["error"] = build_error(
                "INTENT_CLASSIFICATION_FAILED",
                "요청 유형을 분류하지 못했습니다. 다시 시도해주세요.",
                True,
                "llm",
            )
        return state

    async def general_chat_response_node(self, state: AgentState) -> AgentState:
        try:
            response = await self.llm_service.compose_general_chat(
                query=state["query"],
                chat_history=state.get("chat_history", []),
            )
            state["final_response"] = AgentResponse.model_validate(response).model_dump()
            state["final_answer"] = state["final_response"]
        except ValidationError:
            state["error"] = build_error(
                "GENERAL_CHAT_INVALID_RESPONSE",
                "일반 대화 응답 형식이 올바르지 않습니다.",
                True,
                "llm",
            )
            return await self.error_response_node(state)
        except Exception:
            state["error"] = build_error(
                "GENERAL_CHAT_FAILED",
                "답변 생성에 실패했습니다. 다시 시도해주세요.",
                True,
                "llm",
            )
            return await self.error_response_node(state)
        return state

    async def query_refiner_node(self, state: AgentState) -> AgentState:
        if state.get("error"):
            return state
        # Every fashion request needs structured retrieval intent. Even a first
        # text-only turn can describe coordination/similarity or name a target
        # category that downstream retrieval must preserve.
        try:
            raw_refinement = await self.llm_service.refine_query(
                query=state["query"],
                chat_history=state.get("chat_history", []),
                vlm_items=state.get("vlm_items", []),
            )
            refinement = QueryRefinement.model_validate(raw_refinement)
            state["resolved_query"] = refinement.query
            state["request_mode"] = refinement.request_mode
            state["target_category"] = refinement.target_category
        except ValidationError:
            state["error"] = build_error(
                "QUERY_REFINEMENT_INVALID_RESPONSE",
                "검색 질의 정제 결과 형식이 올바르지 않습니다.",
                True,
                "llm",
            )
        except Exception:
            state["error"] = build_error(
                "QUERY_REFINEMENT_FAILED",
                "검색 질의를 정리하지 못했습니다. 다시 시도해주세요.",
                True,
                "llm",
            )
        return state

    async def retrieval_planner_node(self, state: AgentState) -> AgentState:
        if state.get("error"):
            return state
        try:
            raw_plan = await self.llm_service.plan_retrieval(
                query=state.get("resolved_query") or state["query"],
                original_query=state["query"],
                chat_history=state.get("chat_history", []),
                previous_rag_results=state.get("previous_rag_results", []),
                previous_shown_item_refs=state.get("previous_shown_item_refs", []),
                previous_rag_query=state.get("previous_rag_query"),
                previous_retrieval_target=state.get("previous_retrieval_target"),
                closet_items=state.get("closet_items", []),
                user_profile=state.get("user_profile", {}),
                vlm_items=state.get("vlm_items", []),
                use_closet_style=state.get("use_closet_style", True),
            )
            plan = RetrievalPlan.model_validate(raw_plan)
            state["retrieval_action"] = plan.action
            # 홈 피드처럼 호출부가 대상을 정해 둔 경우엔 계획이 덮어쓰지 못한다.
            # 사용자가 이미 가진 옷을 다시 보여주는 화면이 아니기 때문이다.
            state["retrieval_target"] = (
                state["recommendation_target"]
                if state.get("lock_retrieval_target")
                else plan.retrieval_target
            )
            state["candidate_scope"] = plan.candidate_scope
            state["selected_rag_item_refs"] = plan.selected_item_refs
            state["retrieval_reason"] = plan.reason
        except ValidationError:
            state["error"] = build_error(
                "RETRIEVAL_PLAN_INVALID_RESPONSE",
                "검색 계획 결과 형식이 올바르지 않습니다.",
                True,
                "llm",
            )
        except Exception:
            state["error"] = build_error(
                "RETRIEVAL_PLANNING_FAILED",
                "검색 방법을 결정하지 못했습니다. 다시 시도해주세요.",
                True,
                "llm",
            )
        return state

    async def reuse_rag_results_node(self, state: AgentState) -> AgentState:
        refs = state.get("selected_rag_item_refs", [])
        previous_items = state.get("previous_rag_results", [])
        shown_refs = set(state.get("previous_shown_item_refs", []))
        candidate_scope = state.get("candidate_scope", "all")
        items_by_ref: dict[str, dict[str, Any]] = {}
        for item in previous_items:
            for key in ("item_id", "product_url", "image_url"):
                item_ref = str(item.get(key) or "").strip()
                if item_ref:
                    items_by_ref.setdefault(item_ref, item)

        selected = []
        selected_identity_refs: set[str] = set()
        for item_ref in refs:
            item = items_by_ref.get(item_ref)
            if item is None:
                continue
            identity_ref = _item_ref(item)
            if not identity_ref or identity_ref in selected_identity_refs:
                continue
            was_shown = identity_ref in shown_refs
            if candidate_scope == "shown" and not was_shown:
                continue
            if candidate_scope == "unseen" and was_shown:
                continue
            selected.append(item)
            selected_identity_refs.add(identity_ref)
        try:
            normalized = RAGResponse.model_validate({"items": selected, "message": "reused"})
            state["rag_results"] = normalized.model_dump()["items"]
        except ValidationError:
            # Corrupt/stale prior state is not fatal; the graph will run fresh retrieval.
            state["rag_results"] = []
        state["rag_items"] = state["rag_results"]
        state["has_rag_result"] = bool(state["rag_results"])
        state["rag_reused"] = bool(state["rag_results"])
        if not state["rag_reused"]:
            # Keep the trace and downstream cache bookkeeping aligned with the
            # actual path when an invalid/exhausted reuse falls back to RAG.
            state["retrieval_action"] = "retrieve"
        return state

    async def build_rag_request_node(self, state: AgentState) -> AgentState:
        # The LLM refiner has already merged conversation and VLM context.
        context = state.get("context") or {}
        vlm_items = state.get("vlm_items") or []
        user_profile = state.get("user_profile") or {}
        query = state.get("resolved_query") or state["query"]
        request_mode = state.get("request_mode", "direct")
        filters = self._build_rag_filters(
            context,
            user_profile,
            vlm_items,
            query=f"{state['query']} {query}",
            request_mode=request_mode,
            target_category=state.get("target_category"),
        )
        if state.get("candidate_scope") == "unseen":
            excluded_refs = state.get("previous_shown_item_refs", [])
            if excluded_refs:
                filters["excluded_item_refs"] = excluded_refs

        preferred_styles = user_profile.get("preferred_styles") or []
        specificity_filters = dict(filters)
        if "gender" not in context:
            # A saved profile gender narrows the catalog but does not make a vague
            # request stylistically specific. Keep profile taste available as the
            # embedding fallback unless the request supplied gender explicitly.
            specificity_filters.pop("gender", None)
        use_preference_search = bool(
            state.get("use_closet_style", True)
            and preferred_styles
            and is_vague_search_request(
                query,
                request_mode=request_mode,
                has_reference_items=bool(vlm_items),
                filters=specificity_filters,
            )
        )

        request = RAGRequest(
            user_id=state["user_id"],
            query=query,
            retrieval_target=state.get("retrieval_target", "musinsa"),
            user_profile=user_profile,
            vlm_items=vlm_items,
            closet_items=state.get("closet_items") or [],
            use_closet_style=state.get("use_closet_style", True),
            use_preference_search=use_preference_search,
            request_mode=request_mode,
            filters=filters,
            top_k=int(context.get("limit") or context.get("top_k") or DEFAULT_RAG_TOP_K),
        )
        state["rag_query"] = query
        state["rag_request"] = request.model_dump()
        return state

    def _build_rag_filters(
        self,
        context: dict[str, Any],
        user_profile: dict[str, Any],
        vlm_items: list[dict],
        query: str = "",
        request_mode: str = "direct",
        target_category: str | None = None,
    ) -> dict:
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
            "mood",
            "gender",
        ):
            if key in context:
                filters[key] = context[key]

        # 프로필 성별은 카탈로그 조건으로 그대로 내려간다. unisex는 조건이 아니라
        # "가리지 않는다"는 뜻이라 걸지 않는다(걸면 남녀 상품이 전부 빠진다).
        profile_gender = user_profile.get("gender")
        if profile_gender and profile_gender != "unisex" and "gender" not in filters:
            filters["gender"] = profile_gender

        inferred = self._inferred_vlm_filters(vlm_items)
        if target_category and "category" not in filters:
            filters["category"] = target_category

        if request_mode == "coordination":
            # Query Refiner has classified the image as reference context. None of
            # its inferred attributes become candidate hard filters.
            inferred = {}
        elif target_category or query_names_a_category(query):
            # The LLM's candidate category wins over the photographed category.
            # The query fallback preserves main's behavior if a refiner omits the
            # target even though the original request names one.
            inferred.pop("category", None)

        for key, value in inferred.items():
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
            # 트레이스백 없이 문구만 바꾸면 배포 후 원인을 볼 방법이 없다.
            logger.exception("agent node failed: %s", "RAG_INVALID_RESPONSE")
            state["error"] = build_error("RAG_INVALID_RESPONSE", "추천 상품 검색 결과 형식이 올바르지 않습니다.", True, "rag")
            return state
        except Exception:
            # 트레이스백 없이 문구만 바꾸면 배포 후 원인을 볼 방법이 없다.
            logger.exception("agent node failed: %s", "RAG_SEARCH_FAILED")
            state["error"] = build_error("RAG_SEARCH_FAILED", "추천 상품 검색 중 오류가 발생했습니다.", True, "rag")
            return state

        state["rag_request"] = rag_request
        state["rag_results"] = rag_response.get("items", [])
        state["candidate_pool"] = list(state["rag_results"])
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
            "top_k": max(
                int((state.get("rag_request") or {}).get("top_k") or DEFAULT_RAG_TOP_K),
                20,
            ),
        }

        try:
            rag_response = await self.call_rag(rag_request)
        except ValidationError:
            # 트레이스백 없이 문구만 바꾸면 배포 후 원인을 볼 방법이 없다.
            logger.exception("agent node failed: %s", "RAG_INVALID_RESPONSE")
            state["error"] = build_error("RAG_INVALID_RESPONSE", "추천 상품 검색 결과 형식이 올바르지 않습니다.", True, "rag")
            return state
        except Exception:
            # 트레이스백 없이 문구만 바꾸면 배포 후 원인을 볼 방법이 없다.
            logger.exception("agent node failed: %s", "RAG_SEARCH_FAILED")
            state["error"] = build_error("RAG_SEARCH_FAILED", "추천 상품 검색 중 오류가 발생했습니다.", True, "rag")
            return state

        state["fallback_count"] = fallback_count + 1
        state["rag_request"] = rag_request
        state["rag_results"] = rag_response.get("items", [])
        state["candidate_pool"] = list(state["rag_results"])
        state["rag_items"] = state["rag_results"]
        state["has_rag_result"] = bool(state["rag_results"])
        return state

    async def style_ranker_node(self, state: AgentState) -> AgentState:
        if state.get("rag_reused"):
            # The planner's selected refs are already ordered for the follow-up.
            state["ranked_items"] = list(state.get("rag_results", []))
        else:
            candidates = state.get("rag_results", [])
            liked_items = state.get("liked_items", [])
            closet_items = (
                state.get("closet_items", [])
                if state.get("use_closet_style", True)
                else []
            )
            (
                candidate_features,
                liked_features,
                closet_features,
            ) = await self._prepare_personalization_style_features(
                candidates,
                liked_items,
                closet_items,
            )
            ranked = sorted(
                candidates,
                key=lambda item: self._ranking_score(
                    item,
                    state,
                    liked_features,
                    item_features=candidate_features[id(item)],
                    closet_features=closet_features,
                ),
                reverse=True,
            )
            if state.get("diversify_by_category"):
                ranked = self._spread_by_category(ranked)
            state["ranked_items"] = ranked
        return state

    def _spread_by_category(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """카테고리를 번갈아 배치한다.

        홈 타일은 코디 한 벌을 보여주는 자리다. 점수순으로만 자르면 상위가
        한 카테고리(예: 겨울 검정 → 아우터)로 쏠려 LLM에 넘어가는 후보가
        전부 같은 종류가 된다. 각 카테고리의 1등부터 돌아가며 채운다.
        """
        buckets: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            buckets.setdefault(_term(item.get("category")) or "unknown", []).append(item)

        spread: list[dict[str, Any]] = []
        while buckets:
            for category in list(buckets):
                spread.append(buckets[category].pop(0))
                if not buckets[category]:
                    del buckets[category]
        return spread

    def _ranking_score(
        self,
        item: dict[str, Any],
        state: AgentState | None = None,
        liked_features: list[dict[str, Any]] | None = None,
        item_features: dict[str, Any] | None = None,
        closet_features: list[dict[str, Any]] | None = None,
    ) -> float:
        score = self._base_ranking_score(item)
        if state is None:
            return score

        score += self._reference_compatibility_bonus(
            item,
            state.get("vlm_items") or [],
            state.get("request_mode", "direct"),
        )
        score += self._liked_similarity_bonus(
            item,
            state.get("liked_items") or [],
            liked_features=liked_features,
            item_features=item_features,
        )

        # Closet-style mode favors user taste and owned-item compatibility.
        if state.get("use_closet_style", True):
            score += self._preferred_style_bonus(item, state.get("user_profile") or {})
            score += self._closet_personalization_bonus(
                item,
                state.get("closet_items") or [],
                item_features=item_features,
                closet_features=closet_features,
                request_mode=state.get("request_mode", "direct"),
            )
        else:
            score += self._query_relevance_bonus(item, state.get("resolved_query") or state.get("query", ""))
        return score

    def _base_ranking_score(self, item: dict[str, Any]) -> float:
        for key in ("final_score", "metadata_score", "similarity_score"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return 0.0

    def _preferred_style_bonus(self, item: dict[str, Any], user_profile: dict[str, Any]) -> float:
        raw_styles = user_profile.get("preferred_styles", [])
        if isinstance(raw_styles, str):
            raw_styles = [raw_styles]
        preferred_styles: set[str] = set()
        for style in raw_styles:
            normalized_moods = infer_query_intents(str(style))["mood"]
            preferred_styles.update(normalized_moods or split_tokens(style))
        if not preferred_styles:
            return 0.0
        item_terms = _terms_from_item(item, ("mood", "pattern", "category", "label"))
        matched_ratio = len(preferred_styles & item_terms) / len(preferred_styles)
        return PREFERRED_STYLE_MAX_BONUS * matched_ratio

    def _liked_similarity_bonus(
        self,
        item: dict[str, Any],
        liked_items: list[dict[str, Any]],
        *,
        liked_features: list[dict[str, Any]] | None = None,
        item_features: dict[str, Any] | None = None,
    ) -> float:
        """Score only style similarity to the user's saved likes.

        The strongest match represents a user's distinct taste cluster, while
        the mean of the top matches makes repeated preferences more influential.
        A long like list cannot push the score past the fixed cap.
        """
        if not liked_items:
            return 0.0

        prepared_likes = liked_features or [
            self._liked_item_features(liked_item) for liked_item in liked_items
        ]
        candidate = item_features or self._liked_item_features(item)
        similarities = sorted(
            (
                self._liked_item_pair_similarity(candidate, liked_item)
                for liked_item in prepared_likes
            ),
            reverse=True,
        )
        if not similarities or similarities[0] <= 0:
            return 0.0

        profile_similarity = self._top_style_profile_similarity(similarities)
        return min(
            LIKED_ITEM_SIMILARITY_MAX_BONUS * profile_similarity,
            LIKED_ITEM_SIMILARITY_MAX_BONUS,
        )

    async def _prepare_personalization_style_features(
        self,
        candidates: list[dict[str, Any]],
        liked_items: list[dict[str, Any]],
        closet_items: list[dict[str, Any]],
    ) -> tuple[
        dict[int, dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        candidate_features = {
            id(item): self._liked_item_features(item) for item in candidates
        }
        liked_features = [self._liked_item_features(item) for item in liked_items]
        closet_features = [self._liked_item_features(item) for item in closet_items]
        if not candidates or not (liked_items or closet_items):
            return candidate_features, liked_features, closet_features

        try:
            vectors = await self.style_embedding_service.embed_items(
                [*candidates, *liked_items, *closet_items]
            )
            if len(vectors) != len(candidates) + len(liked_items) + len(closet_items):
                raise ValueError("style embedding count does not match input count")
        except Exception:
            # Personalization is optional. An embedding quota/network failure must
            # not turn an otherwise valid recommendation into a failed request.
            logger.warning(
                "personalization style embedding failed; using structured similarity",
                exc_info=True,
            )
            return candidate_features, liked_features, closet_features

        candidate_count = len(candidates)
        liked_end = candidate_count + len(liked_items)
        for item, vector in zip(candidates, vectors[:candidate_count], strict=True):
            candidate_features[id(item)]["style_embedding"] = vector
        for features, vector in zip(
            liked_features,
            vectors[candidate_count:liked_end],
            strict=True,
        ):
            features["style_embedding"] = vector
        for features, vector in zip(
            closet_features,
            vectors[liked_end:],
            strict=True,
        ):
            features["style_embedding"] = vector
        return candidate_features, liked_features, closet_features

    def _liked_item_features(self, item: dict[str, Any]) -> dict[str, Any]:
        # Recommendation entry points enrich likes from product_vectors/closet_items.
        # The saved name is still inferred as a fallback for missing catalog rows.
        # Brand, category, and price never become part of this taste score.
        style_intents = infer_query_intents(
            f"{item.get('item_name') or ''} {item.get('name') or ''}"
        )
        for field in ("color", "fit", "pattern", "mood"):
            raw_value = item.get(field)
            normalized = infer_query_intents(str(raw_value or ""))[field]
            style_intents[field].update(split_tokens(raw_value))
            style_intents[field].update(normalized)
        style_intents["material"] = split_tokens(item.get("material"))
        style_intents["season"].update(
            season_set(item.get("sense_of_season") or item.get("season"))
        )
        return {
            "style_intents": style_intents,
            "style_embedding": None,
            "category": self._style_category(item),
        }

    def _liked_item_pair_similarity(
        self,
        candidate: dict[str, Any],
        liked_item: dict[str, Any],
    ) -> float:
        style_matches: list[float] = []
        for field in ("color", "material", "season", "fit", "pattern", "mood"):
            liked_terms = liked_item["style_intents"].get(field) or set()
            if not liked_terms:
                continue
            candidate_terms = candidate["style_intents"].get(field) or set()
            style_matches.append(len(candidate_terms & liked_terms) / len(liked_terms))

        structured_similarity = (
            sum(style_matches) / len(style_matches) if style_matches else None
        )
        semantic_similarity = cosine_similarity(
            candidate.get("style_embedding") or [],
            liked_item.get("style_embedding") or [],
        )
        if semantic_similarity is None:
            return structured_similarity or 0.0
        if structured_similarity is None:
            return semantic_similarity
        return (
            semantic_similarity * LIKED_ITEM_SEMANTIC_WEIGHT
            + structured_similarity * LIKED_ITEM_STRUCTURED_WEIGHT
        )

    def _closet_personalization_bonus(
        self,
        item: dict[str, Any],
        closet_items: list[dict[str, Any]],
        *,
        item_features: dict[str, Any] | None = None,
        closet_features: list[dict[str, Any]] | None = None,
        request_mode: str = "direct",
    ) -> float:
        if not closet_items:
            return 0.0

        candidate = item_features or self._liked_item_features(item)
        prepared_closet = (
            closet_features
            if closet_features is not None
            else [self._liked_item_features(closet_item) for closet_item in closet_items]
        )
        taste_similarities = sorted(
            (
                self._liked_item_pair_similarity(candidate, closet_item)
                for closet_item in prepared_closet
            ),
            reverse=True,
        )
        taste_similarity = self._top_style_profile_similarity(taste_similarities)
        compatibility = max(
            (
                self._outfit_pair_compatibility(candidate, closet_item)
                for closet_item in prepared_closet
            ),
            default=0.0,
        )

        if request_mode == "coordination":
            taste_weight = COORDINATION_CLOSET_TASTE_WEIGHT
            compatibility_weight = COORDINATION_CLOSET_COMPATIBILITY_WEIGHT
        else:
            taste_weight = CLOSET_TASTE_WEIGHT
            compatibility_weight = CLOSET_COMPATIBILITY_WEIGHT

        combined = taste_similarity * taste_weight + compatibility * compatibility_weight
        return min(
            CLOSET_PERSONALIZATION_MAX_BONUS * combined,
            CLOSET_PERSONALIZATION_MAX_BONUS,
        )

    @staticmethod
    def _top_style_profile_similarity(similarities: list[float]) -> float:
        if not similarities or similarities[0] <= 0:
            return 0.0
        strongest = similarities[0]
        nearest = similarities[:STYLE_PROFILE_DEPTH]
        # Missing neighbors count as zero confidence: one item is weaker evidence
        # than the same style appearing repeatedly in likes or the wardrobe.
        return (strongest * 0.70) + (
            (sum(nearest) / STYLE_PROFILE_DEPTH) * 0.30
        )

    def _outfit_pair_compatibility(
        self,
        candidate: dict[str, Any],
        closet_item: dict[str, Any],
    ) -> float:
        if not self._categories_are_complementary(
            candidate.get("category"),
            closet_item.get("category"),
        ):
            return 0.0

        semantic_similarity = cosine_similarity(
            candidate.get("style_embedding") or [],
            closet_item.get("style_embedding") or [],
        )
        structured_harmony = self._structured_outfit_harmony(candidate, closet_item)
        if semantic_similarity is None:
            return structured_harmony or 0.0
        if structured_harmony is None:
            return semantic_similarity
        return (
            semantic_similarity * OUTFIT_SEMANTIC_WEIGHT
            + structured_harmony * OUTFIT_STRUCTURED_WEIGHT
        )

    @staticmethod
    def _categories_are_complementary(left: Any, right: Any) -> bool:
        return bool(left and right and right in COMPLEMENTARY_CATEGORIES.get(left, set()))

    @staticmethod
    def _style_category(item: dict[str, Any]) -> str | None:
        raw_category = item.get("category") or item.get("label")
        if not raw_category:
            return None
        normalized = normalize_category(raw_category)
        if normalized in COMPLEMENTARY_CATEGORIES:
            return normalized
        inferred = infer_query_intents(str(raw_category))["category"]
        return sorted(inferred)[0] if inferred else None

    def _structured_outfit_harmony(
        self,
        candidate: dict[str, Any],
        closet_item: dict[str, Any],
    ) -> float | None:
        candidate_terms = candidate["style_intents"]
        closet_terms = closet_item["style_intents"]
        field_scores: list[tuple[float, float]] = []

        self._append_harmony_score(
            field_scores,
            0.30,
            self._overlap_harmony(candidate_terms["mood"], closet_terms["mood"]),
        )
        self._append_harmony_score(
            field_scores,
            0.25,
            self._season_harmony(candidate_terms["season"], closet_terms["season"]),
        )
        self._append_harmony_score(
            field_scores,
            0.25,
            self._color_harmony(candidate_terms["color"], closet_terms["color"]),
        )
        self._append_harmony_score(
            field_scores,
            0.10,
            self._pattern_harmony(candidate_terms["pattern"], closet_terms["pattern"]),
        )
        self._append_harmony_score(
            field_scores,
            0.05,
            self._soft_overlap_harmony(
                candidate_terms["material"], closet_terms["material"], mismatch=0.4
            ),
        )
        self._append_harmony_score(
            field_scores,
            0.05,
            self._soft_overlap_harmony(
                candidate_terms["fit"], closet_terms["fit"], mismatch=0.5
            ),
        )
        if not field_scores:
            return None
        total_weight = sum(weight for weight, _score in field_scores)
        return sum(weight * score for weight, score in field_scores) / total_weight

    @staticmethod
    def _append_harmony_score(
        scores: list[tuple[float, float]],
        weight: float,
        score: float | None,
    ) -> None:
        if score is not None:
            scores.append((weight, score))

    @staticmethod
    def _overlap_harmony(left: set[str], right: set[str]) -> float | None:
        if not left or not right:
            return None
        return 1.0 if left & right else 0.0

    @staticmethod
    def _soft_overlap_harmony(
        left: set[str],
        right: set[str],
        *,
        mismatch: float,
    ) -> float | None:
        if not left or not right:
            return None
        return 1.0 if left & right else mismatch

    @staticmethod
    def _season_harmony(left: set[str], right: set[str]) -> float | None:
        if not left or not right:
            return None
        if "all" in left or "all" in right or left & right:
            return 1.0
        if any(frozenset((a, b)) in ADJACENT_SEASON_PAIRS for a in left for b in right):
            return 0.7
        return 0.0

    @staticmethod
    def _color_harmony(left: set[str], right: set[str]) -> float | None:
        if not left or not right:
            return None
        if left & right:
            return 1.0
        if left & NEUTRAL_COLORS or right & NEUTRAL_COLORS:
            return 0.85
        if any(frozenset((a, b)) in COLOR_HARMONY_PAIRS for a in left for b in right):
            return 0.8
        return 0.35

    @staticmethod
    def _pattern_harmony(left: set[str], right: set[str]) -> float | None:
        if not left or not right:
            return None
        if left & right:
            return 1.0
        if "solid" in left or "solid" in right:
            return 0.9
        return 0.2

    def _reference_compatibility_bonus(
        self,
        item: dict[str, Any],
        reference_items: list[dict[str, Any]],
        request_mode: str,
    ) -> float:
        if not reference_items:
            return 0.0

        # Similarity/direct searches value visual attribute overlap. Coordination
        # omits category and gives most weight to shared mood/season coherence.
        if request_mode == "coordination":
            weights = {
                "mood": 0.06,
                "sense_of_season": 0.05,
                "material": 0.03,
                "pattern": 0.03,
                "color": 0.02,
                "fit": 0.01,
            }
        else:
            weights = {
                "category": 0.04,
                "color": 0.04,
                "material": 0.03,
                "fit": 0.03,
                "pattern": 0.02,
                "mood": 0.02,
                "sense_of_season": 0.02,
            }

        reference_terms = {
            field: {
                token
                for reference_item in reference_items
                for token in split_tokens(reference_item.get(field))
            }
            for field in weights
        }
        bonus = sum(
            weight
            for field, weight in weights.items()
            if split_tokens(item.get(field)) & reference_terms[field]
        )
        return min(bonus, REFERENCE_COMPATIBILITY_MAX_BONUS)

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
                state.get("resolved_query") or state["query"],
                state.get("vlm_items", []),
                state.get("ranked_items", []),
                state.get("retrieval_target", "musinsa"),
                closet_items=state.get("closet_items", []),
                use_closet_style=state.get("use_closet_style", True),
                user_profile=state.get("user_profile", {}),
                chat_history=state.get("chat_history", []),
                max_recommendations=state.get("max_recommendations"),
            )
            state["final_response"] = AgentResponse.model_validate(response).model_dump()
            state["final_answer"] = state["final_response"]
        except ValidationError:
            # 트레이스백 없이 문구만 바꾸면 배포 후 원인을 볼 방법이 없다.
            logger.exception("agent node failed: %s", "FINAL_RESPONSE_INVALID")
            state["error"] = build_error("FINAL_RESPONSE_INVALID", "최종 추천 결과 형식이 올바르지 않습니다.", True, "llm")
            return await self.error_response_node(state)
        except Exception:
            # 트레이스백 없이 문구만 바꾸면 배포 후 원인을 볼 방법이 없다.
            logger.exception("agent node failed: %s", "FINAL_RESPONSE_FAILED")
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
        state["final_answer"] = state["final_response"]
        return state
