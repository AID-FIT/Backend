from app.agent.prompts import build_rag_query
from app.agent.state import AgentState
from app.services.llm_service import LlmService
from app.services.rag_service import RagService
from app.services.vlm_service import VlmService


def build_error(code: str, message: str, retryable: bool, source: str) -> dict:
    return {
        "code": code,
        "message": message,
        "retryable": retryable,
        "source": source,
    }


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
        if not state.get("query", "").strip():
            state["error"] = build_error("INVALID_INPUT", "사용자 요청(query)이 비어 있습니다.", False, "agent")
        return state

    async def image_check_node(self, state: AgentState) -> AgentState:
        state["has_image"] = len(state.get("image_urls") or []) > 0
        return state

    async def vlm_node(self, state: AgentState) -> AgentState:
        try:
            vlm_response = await self.vlm_service.analyze_many(state.get("image_urls") or [])
        except Exception:
            state["error"] = build_error("VLM_ANALYSIS_FAILED", "이미지 분석에 실패했습니다. 다시 시도해주세요.", True, "vlm")
            return state

        state["vlm_items"] = vlm_response.get("items", [])
        state["vlm_result"] = vlm_response
        state["is_fashion_item"] = bool(vlm_response.get("is_fashion_item", True))
        return state

    async def fashion_item_check_node(self, state: AgentState) -> AgentState:
        if state.get("has_image") and not state.get("is_fashion_item", False):
            state["error"] = build_error(
                "VLM_NOT_FASHION_ITEM",
                "의류 아이템이 명확하게 보이는 이미지를 업로드해주세요.",
                True,
                "vlm",
            )
        return state

    async def intent_classifier_node(self, state: AgentState) -> AgentState:
        query = state.get("query", "")
        has_closet_items = bool(state.get("closet_items"))

        state["intent"] = "style_recommendation"
        if "무신사" in query or "구매" in query:
            state["retrieval_target"] = "musinsa"
        elif has_closet_items and any(keyword in query for keyword in ["옷장", "내 옷", "가지고"]):
            state["retrieval_target"] = "closet"
        elif has_closet_items:
            state["retrieval_target"] = "hybrid"
        else:
            state["retrieval_target"] = "musinsa"
        return state

    async def build_rag_request_node(self, state: AgentState) -> AgentState:
        context = state.get("context") or {}
        vlm_result = {"items": state.get("vlm_items", [])}
        query = build_rag_query(vlm_result, state["query"])
        state["rag_query"] = query
        state["rag_request"] = {
            "user_id": state.get("user_id"),
            "query": query,
            "retrieval_target": state.get("retrieval_target", "musinsa"),
            "user_profile": state.get("user_profile") or {},
            "closet_items": state.get("closet_items") or [],
            "use_closet_style": state.get("use_closet_style", True),
            "items": state.get("vlm_items", []),
            "filters": {
                "refresh_seed": context.get("refresh_seed", 0),
                "outfit_set": context.get("outfit_set", False),
            },
            "top_k": int(context.get("limit") or context.get("top_k") or 10),
        }
        return state

    async def closet_rag_node(self, state: AgentState) -> AgentState:
        return await self._run_rag(state, "closet")

    async def musinsa_rag_node(self, state: AgentState) -> AgentState:
        return await self._run_rag(state, "musinsa")

    async def hybrid_rag_node(self, state: AgentState) -> AgentState:
        return await self._run_rag(state, "hybrid")

    async def _run_rag(self, state: AgentState, retrieval_target: str) -> AgentState:
        rag_request = {**state["rag_request"], "retrieval_target": retrieval_target}
        try:
            rag_response = await self.rag_service.search_request(rag_request)
        except Exception:
            state["error"] = build_error("RAG_SEARCH_FAILED", "추천 상품 검색 중 오류가 발생했습니다.", True, "rag")
            return state

        state["rag_request"] = rag_request
        state["rag_results"] = rag_response.get("items", [])
        state["rag_items"] = state["rag_results"]
        return state

    async def rag_result_check_node(self, state: AgentState) -> AgentState:
        state["has_rag_result"] = bool(state.get("rag_results"))
        return state

    async def fallback_search_node(self, state: AgentState) -> AgentState:
        rag_request = {
            **state.get("rag_request", {}),
            "filters": {},
            "top_k": max(int(state.get("rag_request", {}).get("top_k") or 10), 20),
        }
        rag_response = await self.rag_service.search_request(rag_request)
        state["rag_request"] = rag_request
        state["rag_results"] = rag_response.get("items", [])
        state["rag_items"] = state["rag_results"]
        state["has_rag_result"] = bool(state["rag_results"])
        return state

    async def style_ranker_node(self, state: AgentState) -> AgentState:
        state["ranked_items"] = sorted(
            state.get("rag_results", []),
            key=lambda item: item.get("final_score") or item.get("similarity_score") or 0,
            reverse=True,
        )
        return state

    async def final_response_node(self, state: AgentState) -> AgentState:
        state["final_response"] = await self.llm_service.compose_recommendation(
            state["query"],
            state.get("vlm_items", []),
            state.get("ranked_items", []),
            state.get("retrieval_target", "musinsa"),
        )
        return state

    async def error_response_node(self, state: AgentState) -> AgentState:
        error = state.get("error") or build_error("FINAL_RESPONSE_FAILED", "최종 추천 결과 생성에 실패했습니다.", True, "agent")
        state["final_response"] = {
            "status": "error",
            "message": error["message"],
            "recommendations": [],
            "style_guide": None,
        }
        return state
