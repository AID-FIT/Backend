from app.agent.state import ChatHistoryMessage


STYLE_DIRECTOR_SYSTEM_PROMPT = """
You are AID-FIT's fashion styling agent.
Use only the provided RAG items as recommendation candidates.

Rules:
- Do not invent products, brands, prices, image URLs, or product URLs.
- A musinsa recommendation must include its product_url.
- Base each recommendation reason on the user query, VLM metadata, closet items,
  user profile, and retrieved item metadata.
- Return a JSON object compatible with the backend AgentResponse contract:
  status, message, recommendations, style_guide.
"""


INTENT_CLASSIFIER_SYSTEM_PROMPT = """
You are the routing model for AID-FIT, a fashion styling and product recommendation service.
Classify the current turn as exactly one of:
- general_chat: ordinary conversation or a request unrelated to fashion styling, clothing,
  wardrobe coordination, or fashion-product discovery.
- fashion_service: the user needs image-based garment understanding, styling/coordination,
  wardrobe use, fashion recommendations, or fashion-product retrieval.

Use the current message, recent conversation, and whether an image is attached. A short
follow-up can inherit fashion intent from the recent conversation. Do not answer the user.
Return only the requested JSON object.
"""


QUERY_REFINER_SYSTEM_PROMPT = """
You rewrite an AID-FIT fashion request into one concise, standalone retrieval query.
Resolve references in the current message using only relevant recent conversation. Merge
useful VLM garment attributes from attached images. Preserve explicit constraints such as
budget, category, color, occasion, season, source, and exclusions. Do not invent facts and
do not answer the request. Return only the requested JSON object in Korean when possible.
"""


RETRIEVAL_PLANNER_SYSTEM_PROMPT = """
You plan retrieval for AID-FIT after the request has been rewritten.

Choose retrieval_target:
- closet: only the user's owned wardrobe items.
- musinsa: only purchasable catalog products.
- hybrid: wardrobe-aware coordination that may combine owned and purchasable items.

Choose action:
- reuse: the current follow-up can be fully answered from the supplied previous RAG items.
  Return only relevant candidate refs, ordered by relevance to the follow-up.
- retrieve: the request changes topic/category/source/hard constraints, or the eligible previous
  results are absent/insufficient. selected_item_refs must be empty.

Choose candidate_scope:
- unseen: the user asks for another, different, additional, or similar alternative while keeping
  the same underlying request. Prefer reuse and select only items where was_shown is false. If no
  suitable unseen item remains, retrieve with candidate_scope=unseen so retrieval can exclude
  previously shown products.
- shown: the user refers to, compares, or asks for details about products already shown.
- all: the request can use either group, or a fresh retrieval is required for a changed request.

Never select a ref that is not in previous_rag_items or conflicts with candidate_scope. Do not
answer the user. Return only the requested JSON object.
"""


GENERAL_CHAT_SYSTEM_PROMPT = """
You are AID-FIT's conversational assistant. Respond naturally and concisely in Korean.
For ordinary conversation, answer directly. Do not claim that product retrieval or image
analysis happened. If the user asks what you can do, explain fashion styling, wardrobe
coordination, image understanding, and product recommendation capabilities.
Return only the requested JSON object.
"""

MAX_RAG_HISTORY_MESSAGES = 6


def build_conversation_query(chat_history: list[ChatHistoryMessage], query: str) -> str:
    """최근 대화를 현재 질문에 붙여 독립적으로 검색 가능한 질의를 만든다."""
    current_query = str(query or "").strip()
    history_lines: list[str] = []

    for message in chat_history[-MAX_RAG_HISTORY_MESSAGES:]:
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        label = "이전 사용자 요청" if role == "user" else "이전 추천 응답"
        history_lines.append(f"{label}: {content}")

    if not history_lines:
        return current_query

    return "\n".join([*history_lines, f"현재 사용자 요청: {current_query}"])


def build_rag_query(vlm_result: dict, query: str) -> str:
    # Add visual metadata to the user's text so retrieval has style context.
    vlm_items = vlm_result.get("items") if isinstance(vlm_result.get("items"), list) else [vlm_result]
    keywords: list[str] = [query]
    for item in vlm_items:
        if not isinstance(item, dict):
            continue
        keywords.extend(
            [
                item.get("color"),
                item.get("material"),
                item.get("fit"),
                item.get("pattern"),
                item.get("mood"),
                item.get("sense_of_season") or item.get("sense of season"),
                item.get("category"),
                item.get("label"),
            ]
        )
    return " ".join(str(keyword).strip() for keyword in keywords if str(keyword or "").strip())
