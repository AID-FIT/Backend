from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    user_id: str | None
    query: str
    image_url: str
    closet_item_id: str | None
    recommendation_target: str
    context: dict[str, Any]
    vlm_result: dict[str, Any]
    is_match: bool
    rag_query: str
    rag_items: list[dict[str, Any]]
    final_answer: dict[str, Any]
    error: str | None
