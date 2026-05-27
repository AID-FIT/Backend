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
