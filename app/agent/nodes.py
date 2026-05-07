from app.agent.prompts import build_rag_query
from app.agent.state import AgentState
from app.services.llm_service import LlmService
from app.services.rag_service import RagService
from app.services.vlm_service import VlmService


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

    async def vlm_node(self, state: AgentState) -> AgentState:
        result = await self.vlm_service.analyze(state["image_url"], state["query"])
        state["vlm_result"] = result
        state["is_match"] = bool(result.get("is_match"))
        if not state["is_match"]:
            state["error"] = "NOT_MATCHING_CLOSET_IMAGE"
        return state

    async def rag_node(self, state: AgentState) -> AgentState:
        query = build_rag_query(state["vlm_result"], state["query"])
        context = state.get("context") or {}
        limit = int(context.get("limit") or 5)
        refresh_seed = int(context.get("refresh_seed") or 0)
        state["rag_query"] = query
        state["rag_items"] = await self.rag_service.search(query, limit=limit, refresh_seed=refresh_seed)
        return state

    async def llm_node(self, state: AgentState) -> AgentState:
        state["final_answer"] = await self.llm_service.compose_recommendation(
            state["query"], state["vlm_result"], state["rag_items"]
        )
        return state
