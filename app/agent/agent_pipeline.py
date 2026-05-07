from uuid import uuid4

from langgraph.graph import END, StateGraph

from app.agent.nodes import AgentNodes
from app.agent.state import AgentState


def route_after_validation(state: AgentState) -> str:
    return "error" if state.get("error") else "image_check"


def route_after_image_check(state: AgentState) -> str:
    return "vlm" if state.get("has_image") else "intent"


def route_after_fashion_check(state: AgentState) -> str:
    return "error" if state.get("error") else "intent"


def retrieval_router(state: AgentState) -> str:
    target = state.get("retrieval_target", "musinsa")
    return target if target in {"closet", "musinsa", "hybrid"} else "musinsa"


def route_after_rag_result_check(state: AgentState) -> str:
    return "style_ranker" if state.get("has_rag_result") else "fallback_search"


def route_after_fallback(state: AgentState) -> str:
    return "style_ranker" if state.get("has_rag_result") else "final_response"


class AidFitAgentPipeline:
    def __init__(self, nodes: AgentNodes | None = None) -> None:
        self.nodes = nodes or AgentNodes()
        self.graph = self._compile()

    def _compile(self):
        graph = StateGraph(AgentState)
        graph.add_node("input_validation", self.nodes.input_validation_node)
        graph.add_node("image_check", self.nodes.image_check_node)
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
            {"image_check": "image_check", "error": "error_response"},
        )
        graph.add_conditional_edges("image_check", route_after_image_check, {"vlm": "vlm", "intent": "intent_classifier"})
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
            {"style_ranker": "style_ranker", "fallback_search": "fallback_search"},
        )
        graph.add_conditional_edges(
            "fallback_search",
            route_after_fallback,
            {"style_ranker": "style_ranker", "final_response": "final_response"},
        )
        graph.add_edge("style_ranker", "final_response")
        graph.add_edge("final_response", END)
        graph.add_edge("error_response", END)
        return graph.compile()

    async def run(
        self,
        query: str,
        image_url: str | None,
        image_urls: list[str] | None = None,
        user_id: str | None = None,
        closet_item_id: str | None = None,
        recommendation_target: str = "musinsa",
        context: dict | None = None,
        user_profile: dict | None = None,
    ) -> dict:
        normalized_image_urls = image_urls or ([image_url] if image_url else [])
        state: AgentState = {
            "user_id": user_id,
            "query": query,
            "image_url": image_url or (normalized_image_urls[0] if normalized_image_urls else None),
            "image_urls": normalized_image_urls,
            "closet_item_id": closet_item_id,
            "recommendation_target": recommendation_target,
            "context": context or {},
            "user_profile": user_profile or {},
            "error": None,
        }
        result = await self.graph.ainvoke(state)
        response = result["final_response"]
        return {
            "request_id": f"rec_{uuid4().hex[:12]}",
            **response,
            "vlm_result": {"items": result.get("vlm_items", [])},
        }
