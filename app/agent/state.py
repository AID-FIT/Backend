from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # Full-stack input
    user_id: str | None
    query: str
    image_urls: list[str]
    user_profile: dict[str, Any]

    # Backward-compatible backend/internal options
    image_url: str | None
    closet_item_id: str | None
    recommendation_target: str
    context: dict[str, Any]

    # Input / image state
    has_image: bool
    is_fashion_item: bool

    # Agent reasoning state
    intent: str
    retrieval_target: str

    # VLM state
    vlm_items: list[dict[str, Any]]
    vlm_result: dict[str, Any]

    # RAG state
    rag_request: dict[str, Any]
    rag_results: list[dict[str, Any]]
    has_rag_result: bool

    # Post-processing state
    ranked_items: list[dict[str, Any]]
    final_response: dict[str, Any]

    # Legacy alias used by older code paths
    rag_query: str
    rag_items: list[dict[str, Any]]
    final_answer: dict[str, Any]

    # Common error state
    error: dict[str, Any] | None
