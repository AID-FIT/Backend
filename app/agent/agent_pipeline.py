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
            "title": "의류 사진을 다시 올려주세요",
            "summary": "업로드된 이미지에서 의류를 안정적으로 확인하지 못했습니다.",
            "tags": ["이미지 확인 필요"],
            "items": [],
        }
        return state

    async def run(
        self, prompt: str, image_url: str, user_id: str | None = None, context: dict | None = None
    ) -> dict:
        state: AgentState = {
            "user_id": user_id,
            "prompt": prompt,
            "image_url": image_url,
            "context": context or {},
            "error": None,
        }
        result = await self.graph.ainvoke(state)
        answer = result["final_answer"]
        return {
            "id": f"rec_{uuid4().hex[:12]}",
            **answer,
            "vlm_result": result.get("vlm_result", {}),
        }

