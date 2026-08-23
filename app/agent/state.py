from typing import Any, Literal, TypedDict


class ChatHistoryMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class AgentState(TypedDict, total=False):
    # Full-stack input
    user_id: str
    query: str
    image_urls: list[str]
    closet_items: list[dict[str, Any]]
    use_closet_style: bool
    user_profile: dict[str, Any]

    # Backward-compatible backend/internal options
    image_url: str | None
    closet_item_id: str | None
    recommendation_target: str
    # True면 검색 계획이 recommendation_target을 덮어쓰지 못한다.
    lock_retrieval_target: bool
    # True면 후보를 카테고리별로 번갈아 배치한다(홈 타일용).
    diversify_by_category: bool
    # 최종 추천 개수 상한. 비면 LlmService 기본값을 쓴다.
    max_recommendations: int | None
    context: dict[str, Any]

    # 후속 질문을 이해하기 위한 직전 대화 내역(시간순)
    chat_history: list[ChatHistoryMessage]

    # 직전 추천 턴의 검색 컨텍스트. 후속 질문이 기존 후보만으로 답할 수
    # 있을 때 RAG를 다시 호출하지 않도록 채팅 계층에서 복원한다.
    previous_rag_results: list[dict[str, Any]]
    previous_shown_item_refs: list[str]
    previous_rag_query: str | None
    previous_retrieval_target: str | None

    # Input / image / closet state
    has_image: bool
    has_closet_items: bool
    is_fashion_item: bool

    # Agent reasoning state
    # 원문 query는 응답/저장에 유지하고, 이 값은 대화 문맥을 포함한 검색에 사용한다.
    resolved_query: str
    intent: Literal["general_chat", "fashion_service"]
    intent_reason: str | None
    retrieval_target: str
    retrieval_action: Literal["reuse", "retrieve"]
    candidate_scope: Literal["all", "shown", "unseen"]
    retrieval_reason: str | None
    selected_rag_item_refs: list[str]
    rag_reused: bool

    # VLM state
    vlm_items: list[dict[str, Any]]
    vlm_result: dict[str, Any]

    # RAG state
    rag_query: str
    rag_request: dict[str, Any]
    rag_results: list[dict[str, Any]]
    candidate_pool: list[dict[str, Any]]
    has_rag_result: bool
    fallback_count: int

    # Post-processing state
    ranked_items: list[dict[str, Any]]
    final_response: dict[str, Any]

    # Legacy aliases used by older code paths
    rag_items: list[dict[str, Any]]
    final_answer: dict[str, Any]

    # Common error state. This is intentionally internal and is not exposed
    # through the public AgentResponse contract.
    error: dict[str, Any] | None
