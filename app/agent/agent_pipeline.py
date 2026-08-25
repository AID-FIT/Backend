from collections.abc import AsyncIterator

from langgraph.graph import END, StateGraph

from app.agent.nodes import AgentNodes
from app.agent.progress import describe_step
from app.agent.state import AgentState, ChatHistoryMessage


def _recommendation_item_refs(response: dict) -> list[str]:
    refs: list[str] = []
    for item in response.get("recommendations") or []:
        if not isinstance(item, dict):
            continue
        for key in ("item_id", "product_url", "image_url"):
            item_ref = str(item.get(key) or "").strip()
            if item_ref:
                refs.append(item_ref)
                break
    return list(dict.fromkeys(refs))


def route_after_validation(state: AgentState) -> str:
    return "error" if state.get("error") else "context_check"


def route_after_intent(state: AgentState) -> str:
    if state.get("error"):
        return "error"
    if state.get("intent") == "general_chat":
        return "general_chat"
    return "vlm" if state.get("has_image") else "query_refiner"


def route_after_fashion_check(state: AgentState) -> str:
    return "error" if state.get("error") else "query_refiner"


def route_after_retrieval_planner(state: AgentState) -> str:
    if state.get("error"):
        return "error"
    return "reuse" if state.get("retrieval_action") == "reuse" else "retrieve"


def route_after_reuse(state: AgentState) -> str:
    if state.get("error"):
        return "error"
    return "style_ranker" if state.get("has_rag_result") else "retrieve"


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


def build_initial_state(
    query: str,
    user_id: str,
    image_urls: list[str] | None = None,
    closet_items: list[dict] | None = None,
    use_closet_style: bool = True,
    user_profile: dict | None = None,
    context: dict | None = None,
    recommendation_target: str = "musinsa",
    lock_retrieval_target: bool = False,
    diversify_by_category: bool = False,
    max_recommendations: int | None = None,
    image_url: str | None = None,
    closet_item_id: str | None = None,
    chat_history: list[ChatHistoryMessage] | None = None,
    previous_rag_results: list[dict] | None = None,
    previous_shown_item_refs: list[str] | None = None,
    previous_rag_query: str | None = None,
    previous_retrieval_target: str | None = None,
) -> AgentState:
    """Build the state shared by the Agent graph and deterministic workflows."""
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
        "lock_retrieval_target": lock_retrieval_target,
        "diversify_by_category": diversify_by_category,
        "max_recommendations": max_recommendations,
        "context": context or {},
        "chat_history": chat_history or [],
        "previous_rag_results": previous_rag_results or [],
        "previous_shown_item_refs": previous_shown_item_refs or [],
        "previous_rag_query": previous_rag_query,
        "previous_retrieval_target": previous_retrieval_target,
        "resolved_query": query,
        "vlm_items": [],
        "rag_results": [],
        "candidate_pool": previous_rag_results or [],
        "ranked_items": [],
        "selected_rag_item_refs": [],
        "candidate_scope": "all",
        "rag_reused": False,
        "fallback_count": 0,
        "error": None,
    }
    return state


def build_trace_result(
    result: AgentState,
    query: str,
    recommendation_target: str = "musinsa",
    previous_shown_item_refs: list[str] | None = None,
) -> dict:
    """Build the trace contract shared by graph and deterministic executions."""
    response = result["final_response"]
    current_shown_item_refs = _recommendation_item_refs(response)
    # An unseen-only request can fall through from an exhausted reuse
    # cache to fresh RAG while retrieval_action still says "reuse".
    # The scope, rather than the original action, determines whether
    # previously shown refs must stay excluded on following turns.
    preserve_shown_history = bool(result.get("rag_reused")) or (
        result.get("candidate_scope") == "unseen"
    )
    shown_item_refs = list(
        dict.fromkeys(
            [
                *((previous_shown_item_refs or []) if preserve_shown_history else []),
                *current_shown_item_refs,
            ]
        )
    )
    candidate_pool = (
        result.get("candidate_pool", [])
        if result.get("intent") == "fashion_service"
        else []
    )
    return {
        "response": response,
        "vlm_items": result.get("vlm_items", []),
        "ranked_items": result.get("ranked_items", []),
        "rag_items": result.get("rag_results", []),
        "candidate_pool": candidate_pool,
        "shown_item_refs": shown_item_refs,
        "retrieval_target": result.get("retrieval_target", recommendation_target),
        "intent": result.get("intent"),
        "intent_reason": result.get("intent_reason"),
        "resolved_query": result.get("resolved_query", query),
        "rag_query": result.get("rag_query"),
        "retrieval_action": result.get("retrieval_action"),
        "candidate_scope": result.get("candidate_scope", "all"),
        "retrieval_reason": result.get("retrieval_reason"),
        "rag_reused": result.get("rag_reused", False),
        "selected_rag_item_refs": result.get("selected_rag_item_refs", []),
        "error": result.get("error"),
    }


class AidFitAgentPipeline:
    def __init__(self, nodes: AgentNodes | None = None) -> None:
        self.nodes = nodes or AgentNodes()
        self.graph = self._compile()

    def _compile(self):
        # Build the recommendation workflow as an explicit state machine.
        graph = StateGraph(AgentState)
        graph.add_node("input_validation", self.nodes.input_validation_node)
        graph.add_node("context_check", self.nodes.context_check_node)
        graph.add_node("intent_classifier", self.nodes.intent_classifier_node)
        graph.add_node("general_chat_response", self.nodes.general_chat_response_node)
        graph.add_node("vlm", self.nodes.vlm_node)
        graph.add_node("fashion_item_check", self.nodes.fashion_item_check_node)
        graph.add_node("query_refiner", self.nodes.query_refiner_node)
        graph.add_node("retrieval_planner", self.nodes.retrieval_planner_node)
        graph.add_node("reuse_rag_results", self.nodes.reuse_rag_results_node)
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
        graph.add_edge("context_check", "intent_classifier")
        graph.add_conditional_edges(
            "intent_classifier",
            route_after_intent,
            {
                "general_chat": "general_chat_response",
                "vlm": "vlm",
                "query_refiner": "query_refiner",
                "error": "error_response",
            },
        )
        graph.add_edge("general_chat_response", END)
        graph.add_edge("vlm", "fashion_item_check")
        graph.add_conditional_edges(
            "fashion_item_check",
            route_after_fashion_check,
            {"query_refiner": "query_refiner", "error": "error_response"},
        )
        graph.add_edge("query_refiner", "retrieval_planner")
        graph.add_conditional_edges(
            "retrieval_planner",
            route_after_retrieval_planner,
            {"reuse": "reuse_rag_results", "retrieve": "build_rag_request", "error": "error_response"},
        )
        graph.add_conditional_edges(
            "reuse_rag_results",
            route_after_reuse,
            {"style_ranker": "style_ranker", "retrieve": "build_rag_request", "error": "error_response"},
        )
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

    def _build_initial_state(
        self,
        query: str,
        user_id: str,
        image_urls: list[str] | None = None,
        closet_items: list[dict] | None = None,
        use_closet_style: bool = True,
        user_profile: dict | None = None,
        context: dict | None = None,
        recommendation_target: str = "musinsa",
        lock_retrieval_target: bool = False,
        diversify_by_category: bool = False,
        max_recommendations: int | None = None,
        image_url: str | None = None,
        closet_item_id: str | None = None,
        chat_history: list[ChatHistoryMessage] | None = None,
        previous_rag_results: list[dict] | None = None,
        previous_shown_item_refs: list[str] | None = None,
        previous_rag_query: str | None = None,
        previous_retrieval_target: str | None = None,
    ) -> AgentState:
        return build_initial_state(
            query=query,
            user_id=user_id,
            image_urls=image_urls,
            closet_items=closet_items,
            use_closet_style=use_closet_style,
            user_profile=user_profile,
            context=context,
            recommendation_target=recommendation_target,
            lock_retrieval_target=lock_retrieval_target,
            diversify_by_category=diversify_by_category,
            max_recommendations=max_recommendations,
            image_url=image_url,
            closet_item_id=closet_item_id,
            chat_history=chat_history,
            previous_rag_results=previous_rag_results,
            previous_shown_item_refs=previous_shown_item_refs,
            previous_rag_query=previous_rag_query,
            previous_retrieval_target=previous_retrieval_target,
        )

    def _build_trace_result(
        self,
        result: AgentState,
        query: str,
        recommendation_target: str = "musinsa",
        previous_shown_item_refs: list[str] | None = None,
    ) -> dict:
        return build_trace_result(
            result,
            query=query,
            recommendation_target=recommendation_target,
            previous_shown_item_refs=previous_shown_item_refs,
        )

    async def run(self, return_trace: bool = False, **kwargs) -> dict:
        state = self._build_initial_state(**kwargs)
        result = await self.graph.ainvoke(state)
        if return_trace:
            return self._build_trace_result(
                result,
                query=kwargs["query"],
                recommendation_target=kwargs.get("recommendation_target", "musinsa"),
                previous_shown_item_refs=kwargs.get("previous_shown_item_refs"),
            )
        return result["final_response"]

    async def stream(self, **kwargs) -> AsyncIterator[dict]:
        """노드가 끝날 때마다 진행 상황을, 마지막에 결과를 흘린다.

        사용자는 13초를 기다리는 동안 스켈레톤만 봤다. 어디까지 왔는지
        알려주려면 그래프가 도는 중간에 내보낼 수 있어야 한다.
        """
        state = self._build_initial_state(**kwargs)
        # astream은 각 노드가 바꾼 부분만 준다. 진행 문구가 참조할 수 있도록
        # 여기서 하나로 합쳐 둔다.
        merged: dict = dict(state)

        async for update in self.graph.astream(state, stream_mode="updates"):
            for node_name, delta in update.items():
                if isinstance(delta, dict):
                    merged.update(delta)
                step = describe_step(node_name, merged)
                if step is not None:
                    yield {"type": "step", **step}

        yield {
            "type": "result",
            **self._build_trace_result(
                merged,  # type: ignore[arg-type]
                query=kwargs["query"],
                recommendation_target=kwargs.get("recommendation_target", "musinsa"),
                previous_shown_item_refs=kwargs.get("previous_shown_item_refs"),
            ),
        }
