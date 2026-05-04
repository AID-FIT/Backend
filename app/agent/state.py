from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    user_id: str | None
    prompt: str
    image_url: str
    context: dict[str, Any]
    vlm_result: dict[str, Any]
    is_clothing: bool
    rag_query: str
    rag_items: list[dict[str, Any]]
    final_answer: dict[str, Any]
    error: str | None

