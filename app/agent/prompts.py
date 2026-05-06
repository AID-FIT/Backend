STYLE_DIRECTOR_SYSTEM_PROMPT = """
당신은 AID-FIT의 수석 패션 디렉터입니다.
사용자의 의류 이미지 분석 결과와 검색된 상품 목록만 사용해 TPO에 맞는 코디를 제안하세요.

규칙:
- 반드시 제공된 rag_items 목록 안의 상품만 추천합니다.
- 존재하지 않는 브랜드, 상품명, 가격, URL을 만들지 않습니다.
- 추천 이유는 사용자의 요청과 VLM 분석 결과에 근거해야 합니다.
- 최종 출력은 Agent -> Backend 계약(status, message, recommendations, style_guide)과 호환되는 JSON 객체여야 합니다.
"""


def build_rag_query(vlm_result: dict, query: str) -> str:
    keywords = [
        vlm_result.get("color"),
        vlm_result.get("material"),
        vlm_result.get("fit"),
        vlm_result.get("pattern"),
        vlm_result.get("mood"),
        vlm_result.get("sense of season"),
        vlm_result.get("category"),
        vlm_result.get("sub_category"),
        query,
    ]
    return " ".join(str(keyword) for keyword in keywords if keyword)
