from uuid import uuid4

from langgraph.graph import END, StateGraph

from app.agent.nodes import AgentNodes
from app.agent.state import AgentState


def route_after_vlm(state: AgentState) -> str:
    return "fallback" if state.get("error") else "rag"


class AidFitAgentPipeline:
    def __init__(self, nodes: AgentNodes | None = None) -> None:
        self.nodes = nodes or AgentNodes()
        self.graph = self._compile()

    def _compile(self):
        graph = StateGraph(AgentState)
        graph.add_node("vlm", self.nodes.vlm_node)
        graph.add_node("rag", self.nodes.rag_node)
        graph.add_node("llm", self.nodes.llm_node)
        graph.add_node("fallback", self._fallback_node)
        graph.set_entry_point("vlm")
        graph.add_conditional_edges("vlm", route_after_vlm, {"rag": "rag", "fallback": "fallback"})
        graph.add_edge("rag", "llm")
        graph.add_edge("llm", END)
        graph.add_edge("fallback", END)
        return graph.compile()

    async def _fallback_node(self, state: AgentState) -> AgentState:
        state["final_answer"] = {
            "status": "fallback",
            "message": "업로드된 옷장 이미지가 요청 조건과 충분히 매칭되지 않았습니다.",
            "recommendations": [],
            "style_guide": {
                "summary": "이미지 확인 필요",
                "tips": ["분석 가능한 의류 사진을 다시 업로드해 주세요."],
            },
        }
        return state

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
        answer = result["final_answer"]
        return {
            "request_id": f"rec_{uuid4().hex[:12]}",
            **answer,
            "vlm_result": result.get("vlm_result", {}),
        }
