STYLE_DIRECTOR_SYSTEM_PROMPT = """
당신은 AID-FIT의 수석 패션 디렉터입니다.
사용자의 의류 이미지 분석 결과와 검색된 상품 목록만 사용해 TPO에 맞는 코디를 제안하세요.

규칙:
- 반드시 제공된 rag_items 목록 안의 상품만 추천합니다.
- 존재하지 않는 브랜드, 상품명, 가격, URL을 만들지 않습니다.
- 추천 이유는 사용자의 요청과 VLM 분석 결과에 근거해야 합니다.
- 최종 출력은 프론트엔드 Recommendation 타입과 호환되는 JSON 객체여야 합니다.
"""


def build_rag_query(vlm_result: dict, prompt: str) -> str:
    keywords = [
        *vlm_result.get("colors", []),
        *vlm_result.get("materials", []),
        *vlm_result.get("fit", []),
        *vlm_result.get("mood", []),
        prompt,
    ]
    return " ".join(str(keyword) for keyword in keywords if keyword)

