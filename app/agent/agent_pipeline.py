from langgraph.graph import END, StateGraph

from app.agent.nodes import AgentNodes
from app.agent.state import AgentState


def route_after_validation(state: AgentState) -> str:
    return "error" if state.get("error") else "context_check"


def route_after_context_check(state: AgentState) -> str:
    return "vlm" if state.get("has_image") else "intent"


def route_after_fashion_check(state: AgentState) -> str:
    return "error" if state.get("error") else "intent"


def retrieval_router(state: AgentState) -> str:
    target = state.get("retrieval_target", "musinsa")
    return target if target in {"closet", "musinsa", "hybrid"} else "musinsa"


def route_after_rag_result_check(state: AgentState) -> str:
    if state.get("error"):
        return "error"
    if state.get("has_rag_result"):
        return "style_ranker"
    # Stop after one relaxed retry to avoid looping on empty searches.
    if int(state.get("fallback_count") or 0) >= 1:
        return "final_response"
    return "fallback_search"


def route_after_fallback(state: AgentState) -> str:
    if state.get("error"):
        return "error"
    return "style_ranker" if state.get("has_rag_result") else "final_response"


class AidFitAgentPipeline:
    def __init__(self, nodes: AgentNodes | None = None) -> None:
        self.nodes = nodes or AgentNodes()
        self.graph = self._compile()

    def _compile(self):
        # Build the recommendation workflow as an explicit state machine.
        graph = StateGraph(AgentState)
        graph.add_node("input_validation", self.nodes.input_validation_node)
        graph.add_node("context_check", self.nodes.context_check_node)
        graph.add_node("vlm", self.nodes.vlm_node)
        graph.add_node("fashion_item_check", self.nodes.fashion_item_check_node)
        graph.add_node("intent_classifier", self.nodes.intent_classifier_node)
        graph.add_node("build_rag_request", self.nodes.build_rag_request_node)
        graph.add_node("closet_rag", self.nodes.closet_rag_node)
        graph.add_node("musinsa_rag", self.nodes.musinsa_rag_node)
        graph.add_node("hybrid_rag", self.nodes.hybrid_rag_node)
        graph.add_node("rag_result_check", self.nodes.rag_result_check_node)
        graph.add_node("fallback_search", self.nodes.fallback_search_node)
        graph.add_node("style_ranker", self.nodes.style_ranker_node)
        graph.add_node("final_response", self.nodes.final_response_node)
        graph.add_node("error_response", self.nodes.error_response_node)

        graph.set_entry_point("input_validation")
        graph.add_conditional_edges(
            "input_validation",
            route_after_validation,
            {"context_check": "context_check", "error": "error_response"},
        )
        graph.add_conditional_edges(
            "context_check",
            route_after_context_check,
            {"vlm": "vlm", "intent": "intent_classifier"},
        )
        graph.add_edge("vlm", "fashion_item_check")
        graph.add_conditional_edges(
            "fashion_item_check",
            route_after_fashion_check,
            {"intent": "intent_classifier", "error": "error_response"},
        )
        graph.add_edge("intent_classifier", "build_rag_request")
        graph.add_conditional_edges(
            "build_rag_request",
            retrieval_router,
            {"closet": "closet_rag", "musinsa": "musinsa_rag", "hybrid": "hybrid_rag"},
        )
        graph.add_edge("closet_rag", "rag_result_check")
        graph.add_edge("musinsa_rag", "rag_result_check")
        graph.add_edge("hybrid_rag", "rag_result_check")
        graph.add_conditional_edges(
            "rag_result_check",
            route_after_rag_result_check,
            {
                "style_ranker": "style_ranker",
                "fallback_search": "fallback_search",
                "final_response": "final_response",
                "error": "error_response",
            },
        )
        graph.add_conditional_edges(
            "fallback_search",
            route_after_fallback,
            {"style_ranker": "style_ranker", "final_response": "final_response", "error": "error_response"},
        )
        graph.add_edge("style_ranker", "final_response")
        graph.add_edge("final_response", END)
        graph.add_edge("error_response", END)
        return graph.compile()

    async def run(
        self,
        query: str,
        user_id: str,
        image_urls: list[str] | None = None,
        closet_items: list[dict] | None = None,
        use_closet_style: bool = True,
        user_profile: dict | None = None,
        context: dict | None = None,
        recommendation_target: str = "musinsa",
        image_url: str | None = None,
        closet_item_id: str | None = None,
    ) -> dict:
        # Keep old single-image callers compatible with the new multi-image path.
        normalized_image_urls = image_urls or ([image_url] if image_url else [])
        state: AgentState = {
            "user_id": user_id,
            "query": query,
            "image_url": image_url or (normalized_image_urls[0] if normalized_image_urls else None),
            "image_urls": normalized_image_urls,
            "closet_items": closet_items or [],
            "use_closet_style": use_closet_style,
            "user_profile": user_profile or {},
            "closet_item_id": closet_item_id,
            "recommendation_target": recommendation_target,
            "context": context or {},
            "vlm_items": [],
            "rag_results": [],
            "ranked_items": [],
            "fallback_count": 0,
            "error": None,
        }
        result = await self.graph.ainvoke(state)
        return result["final_response"]
