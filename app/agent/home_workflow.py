"""Deterministic recommendation workflow used only by the home feed.

The chat Agent still owns intent classification, query refinement, retrieval
planning, candidate reuse, and VLM routing. Home requests already know their
intent and retrieval source, so this workflow calls only the shared retrieval,
ranking, and final-response nodes.
"""

import logging
from collections.abc import AsyncIterator

from app.agent.agent_pipeline import build_initial_state, build_trace_result
from app.agent.nodes import AgentNodes, build_error
from app.agent.progress import describe_step
from app.agent.state import AgentState
from app.schemas.recommendation import AgentResponse
from app.services.llm_service import empty_recommendation_response


logger = logging.getLogger(__name__)


class HomeRecommendationWorkflow:
    """Run the fixed home path without invoking the common LangGraph graph."""

    def __init__(self, nodes: AgentNodes | None = None) -> None:
        self.nodes = nodes or AgentNodes()

    def _build_initial_state(
        self,
        query: str,
        user_id: str,
        closet_items: list[dict] | None = None,
        use_closet_style: bool = True,
        user_profile: dict | None = None,
        context: dict | None = None,
        diversify_by_category: bool = False,
        max_recommendations: int | None = None,
    ) -> AgentState:
        state = build_initial_state(
            query=query,
            user_id=user_id,
            image_urls=[],
            closet_items=closet_items,
            use_closet_style=use_closet_style,
            user_profile=user_profile,
            context=context,
            recommendation_target="musinsa",
            lock_retrieval_target=True,
            diversify_by_category=diversify_by_category,
            max_recommendations=max_recommendations,
            chat_history=[],
            previous_rag_results=[],
            previous_shown_item_refs=[],
        )
        state.update(
            {
                "has_image": False,
                "has_closet_items": bool(closet_items),
                "intent": "fashion_service",
                "intent_reason": "fixed home recommendation intent",
                "resolved_query": query,
                "retrieval_target": "musinsa",
                "retrieval_action": "retrieve",
                "candidate_scope": "all",
                "retrieval_reason": "fixed home retrieval path",
                "selected_rag_item_refs": [],
                "rag_reused": False,
            }
        )
        return state

    async def _execute(self, state: AgentState) -> AsyncIterator[str]:
        """Mutate ``state`` in execution order and yield completed node names."""
        try:
            state.update(await self.nodes.input_validation_node(state))
            yield "input_validation"
            if state.get("error"):
                state.update(await self.nodes.error_response_node(state))
                yield "error_response"
                return

            # These values are code-owned invariants for every home request.
            state.update(
                {
                    "intent": "fashion_service",
                    "resolved_query": str(state["query"]).strip(),
                    "retrieval_target": "musinsa",
                    "retrieval_action": "retrieve",
                    "candidate_scope": "all",
                    "selected_rag_item_refs": [],
                    "rag_reused": False,
                }
            )

            state.update(await self.nodes.build_rag_request_node(state))
            yield "build_rag_request"

            state.update(await self.nodes.musinsa_rag_node(state))
            yield "musinsa_rag"
            if state.get("error"):
                state.update(await self.nodes.error_response_node(state))
                yield "error_response"
                return

            state.update(await self.nodes.rag_result_check_node(state))
            yield "rag_result_check"

            if not state.get("has_rag_result"):
                state.update(await self.nodes.fallback_search_node(state))
                yield "fallback_search"
                if state.get("error"):
                    state.update(await self.nodes.error_response_node(state))
                    yield "error_response"
                    return

            if not state.get("has_rag_result"):
                response = AgentResponse.model_validate(
                    empty_recommendation_response()
                ).model_dump()
                state["final_response"] = response
                state["final_answer"] = response
                yield "final_response"
                return

            state.update(await self.nodes.style_ranker_node(state))
            yield "style_ranker"

            state.update(await self.nodes.final_response_node(state))
            yield "error_response" if state.get("error") else "final_response"
        except Exception:
            logger.exception("home recommendation workflow failed")
            state["error"] = build_error(
                "HOME_RECOMMENDATION_FAILED",
                "홈 추천을 생성하는 중 오류가 발생했습니다.",
                True,
                "agent",
            )
            state.update(await self.nodes.error_response_node(state))
            yield "error_response"

    def _build_trace_result(self, state: AgentState, query: str) -> dict:
        return build_trace_result(state, query=query, recommendation_target="musinsa")

    async def run(self, return_trace: bool = False, **kwargs) -> dict:
        state = self._build_initial_state(**kwargs)
        async for _node_name in self._execute(state):
            pass
        trace = self._build_trace_result(state, query=kwargs["query"])
        return trace if return_trace else trace["response"]

    async def stream(self, **kwargs) -> AsyncIterator[dict]:
        state = self._build_initial_state(**kwargs)
        async for node_name in self._execute(state):
            step = describe_step(node_name, state)
            if step is not None:
                yield {"type": "step", **step}

        yield {
            "type": "result",
            **self._build_trace_result(state, query=kwargs["query"]),
        }
