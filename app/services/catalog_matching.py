"""카탈로그 검색에 쓰는 순수 로직 — 별칭 확장, 의도 추출, 메타데이터 점수.

ChromaDB 구현(`rag_service_final.py`)에 있던 것을 저장소와 무관한 형태로
옮겨 왔다. pgvector로 검색을 다시 쓰면서 이 계층이 통째로 빠져 있었다.
빠진 결과는 조용했다 — 검색은 성공하고 결과도 나오는데, 색·무드·계절이
순위에 아무 영향을 주지 못하고 한글 질의가 영문 카탈로그와 만나지 못했다.

여기에는 chromadb도 DB 세션도 들어오지 않는다. 저장소를 또 갈아끼워도
이 계층은 따라와야 하기 때문이다.

주의: `rag_service_final.py`에도 같은 표와 점수 로직이 남아 있다. static 경로를
걷어낼 때 그쪽이 이 모듈을 import하도록 바꾼다.
"""

import re
from typing import Any

# 최종 순위 = 벡터 유사도 × 0.75 + 메타데이터 일치 × 0.25.
# 유사도만 쓰면 "검정 스트릿"을 요청해도 색이 전혀 다른 상품이 위로 온다.
SIMILARITY_WEIGHT = 0.75
METADATA_WEIGHT = 0.25

# 임베딩 질의가 길어지면 뒤쪽 토큰이 묻힌다.
MAX_SEARCH_WORDS = 120

# --- 별칭 표 (ChromaDB 구현에서 옮겨 옴) ---

QUERY_ALIASES: dict[str, list[str]] = {
    "검정": ["black", "검은색", "블랙", "까만색"],
    "검은색": ["black", "검정", "블랙", "까만색"],
    "블랙": ["black", "검정", "검은색"],
    "까만색": ["black", "검정", "검은색", "블랙"],
    "어두운색": ["black", "charcoal", "navy", "brown", "다크톤"],
    "어두운 색": ["black", "charcoal", "navy", "brown", "다크톤"],
    "다크톤": ["black", "charcoal", "navy", "brown", "어두운색"],
    "흰색": ["white", "하얀색", "화이트"],
    "하얀색": ["white", "흰색", "화이트"],
    "화이트": ["white", "흰색", "하얀색"],
    "회색": ["gray", "그레이"],
    "그레이": ["gray", "회색"],
    "무채색": ["black", "white", "gray", "charcoal"],
    "아이보리": ["ivory", "오프화이트"],
    "오프화이트": ["ivory", "아이보리"],
    "베이지": ["beige"],
    "브라운": ["brown", "갈색"],
    "갈색": ["brown", "브라운"],
    "네이비": ["navy", "남색"],
    "남색": ["navy", "네이비"],
    "파랑": ["blue", "파란색", "블루"],
    "파란색": ["blue", "파랑", "블루"],
    "블루": ["blue", "파랑", "파란색"],
    "하늘색": ["sky blue", "스카이블루"],
    "스카이블루": ["sky blue", "하늘색"],
    "데님": ["denim blue", "청색"],
    "청색": ["denim blue", "데님"],
    "초록": ["green", "초록색", "그린"],
    "초록색": ["green", "초록", "그린"],
    "그린": ["green", "초록", "초록색"],
    "카키": ["khaki", "olive"],
    "올리브": ["khaki", "olive"],
    "민트": ["mint"],
    "빨강": ["red", "빨간색", "레드"],
    "빨간색": ["red", "빨강", "레드"],
    "레드": ["red", "빨강", "빨간색"],
    "붉은계열": ["red", "burgundy", "pink", "버건디", "와인색"],
    "붉은 계열": ["red", "burgundy", "pink", "버건디", "와인색"],
    "와인색": ["burgundy", "red", "붉은계열"],
    "버건디": ["burgundy", "red", "와인색", "붉은계열"],
    "핑크": ["pink", "분홍"],
    "분홍": ["pink", "핑크"],
    "보라": ["purple", "퍼플"],
    "퍼플": ["purple", "보라"],
    "노랑": ["yellow", "옐로우"],
    "옐로우": ["yellow", "노랑"],
    "주황": ["orange", "오렌지"],
    "오렌지": ["orange", "주황"],
    "실버": ["silver", "은색"],
    "은색": ["silver", "실버"],
    "골드": ["gold", "금색"],
    "금색": ["gold", "골드"],
    "봄": ["spring"],
    "여름": ["summer"],
    "가을": ["fall"],
    "겨울": ["winter"],
    "간절기": ["spring", "fall"],
    "봄가을": ["spring", "fall"],
    "사계절": ["all"],
    "올시즌": ["all"],
    "오버핏": ["oversized", "오버사이즈", "박시한"],
    "오버사이즈": ["oversized", "오버핏", "박시한"],
    "박시한": ["oversized", "오버핏"],
    "넉넉한": ["oversized", "loose"],
    "루즈핏": ["loose", "여유있는"],
    "여유있는": ["loose", "루즈핏"],
    "기본핏": ["regular", "정핏", "레귤러"],
    "정핏": ["regular", "기본핏", "레귤러"],
    "레귤러": ["regular", "기본핏", "정핏"],
    "슬림": ["slim", "슬림핏"],
    "슬림핏": ["slim", "슬림"],
    "와이드": ["wide"],
    "스트레이트": ["straight", "일자핏"],
    "일자핏": ["straight", "스트레이트"],
    "부츠컷": ["bootcut"],
    "플레어": ["flare"],
    "크롭": ["cropped", "짧은기장"],
    "짧은기장": ["cropped", "mini"],
    "세미와이드": ["semi-wide"],
    "미니": ["mini"],
    "미디": ["midi"],
    "맥시": ["maxi", "긴기장"],
    "긴기장": ["maxi"],
    "키높이": ["platform"],
    "플랫폼": ["platform"],
    "로우탑": ["low-top"],
    "미드탑": ["mid-top"],
    "하이탑": ["high-top"],
    "무지": ["solid", "단색"],
    "단색": ["solid", "무지"],
    "스트라이프": ["striped", "줄무늬"],
    "줄무늬": ["striped", "스트라이프"],
    "체크": ["plaid", "check"],
    "도트": ["dot", "물방울"],
    "물방울": ["dot", "도트"],
    "꽃무늬": ["floral", "플로럴"],
    "플로럴": ["floral", "꽃무늬"],
    "로고": ["logo"],
    "레터링": ["lettering"],
    "그래픽": ["graphic", "프린팅"],
    "프린팅": ["graphic", "그래픽"],
    "배색": ["color block", "컬러블록"],
    "컬러블록": ["color block", "배색"],
    "카모": ["camouflage", "밀리터리"],
    "밀리터리": ["camouflage", "카모"],
    "호피": ["leopard", "레오파드"],
    "레오파드": ["leopard", "호피"],
    "자수": ["embroidered"],
    "골지": ["ribbed"],
    "퀼팅": ["quilted"],
    "워싱": ["washed"],
    "빈티지": ["vintage", "distressed"],
    "디스트로이드": ["distressed"],
    "레이스": ["lace"],
    "메쉬": ["mesh"],
    "시스루": ["sheer"],
    "캐주얼": ["casual", "데일리", "편한"],
    "데일리": ["casual", "minimal"],
    "편한": ["casual"],
    "편안한": ["casual"],
    "미니멀": ["minimal", "깔끔한", "심플한"],
    "깔끔한": ["minimal", "classic"],
    "심플한": ["minimal"],
    "단정한": ["minimal", "classic", "formal"],
    "스트릿": ["street", "힙한"],
    "힙한": ["street"],
    "스포티": ["sporty", "스포츠"],
    "스포츠": ["sporty"],
    "활동적인": ["sporty"],
    "포멀": ["formal"],
    "격식있는": ["formal"],
    "출근룩": ["formal", "minimal"],
    "오피스룩": ["formal", "minimal"],
    "클래식": ["classic"],
    "기본": ["classic", "regular"],
    "베이직": ["classic", "minimal"],
    "시크": ["chic"],
    "세련된": ["chic", "classic"],
    "로맨틱": ["romantic"],
    "페미닌": ["feminine"],
    "여성스러운": ["feminine"],
    "레트로": ["retro"],
    "프레피": ["preppy"],
    "아웃도어": ["outdoor"],
    "유틸리티": ["utility"],
    "실용적인": ["utility"],
    "귀여운": ["cute"],
    "러블리한": ["cute"],
    "하의": ["바지"],
    "팬츠": ["바지"],
    "bottom": ["바지"],
    "pants": ["바지"],
    "상의": ["상의"],
    "top": ["상의"],
    "아우터": ["아우터"],
    "outer": ["아우터"],
    "모자": ["모자"],
    "cap": ["모자"],
    "hat": ["모자"],
    "신발": ["신발"],
    "shoes": ["신발"],
    "운동화": ["스니커즈", "신발"],
    "가방": ["가방"],
    "bag": ["가방"],
    "원피스": ["원피스/스커트"],
    "dress": ["원피스/스커트"],
    "스커트": ["원피스/스커트"],
    "skirt": ["원피스/스커트"],
    "반팔": ["반팔티", "상의"],
    "반팔티": ["반팔티", "상의"],
    "티셔츠": ["반팔티", "상의"],
    "셔츠": ["셔츠", "상의"],
    "니트": ["니트", "상의"],
    "슬랙스": ["슬랙스", "바지"],
    "와이드팬츠": ["와이드팬츠", "바지"],
    "와이드 팬츠": ["와이드팬츠", "바지"],
    "스니커즈": ["스니커즈", "신발"],
}

CATEGORY_ALIASES = {
    "하의": "바지",
    "팬츠": "바지",
    "bottom": "바지",
    "pants": "바지",
    "바지": "바지",
    "상의": "상의",
    "top": "상의",
    "아우터": "아우터",
    "outer": "아우터",
    "모자": "모자",
    "cap": "모자",
    "hat": "모자",
    "신발": "신발",
    "shoes": "신발",
    "가방": "가방",
    "bag": "가방",
    "원피스": "원피스/스커트",
    "스커트": "원피스/스커트",
    "dress": "원피스/스커트",
    "skirt": "원피스/스커트",
    "원피스/스커트": "원피스/스커트",
    "원피스·스커트": "원피스/스커트",
}

GENDER_ALIASES = {
    "남자": "men",
    "남성": "men",
    "men": "men",
    "male": "men",
    "여자": "women",
    "여성": "women",
    "women": "women",
    "female": "women",
}

TOKEN_TO_INTENT_FIELD = {
    "black": "color",
    "white": "color",
    "gray": "color",
    "charcoal": "color",
    "ivory": "color",
    "beige": "color",
    "brown": "color",
    "navy": "color",
    "blue": "color",
    "sky blue": "color",
    "denim blue": "color",
    "green": "color",
    "khaki": "color",
    "olive": "color",
    "mint": "color",
    "red": "color",
    "burgundy": "color",
    "pink": "color",
    "purple": "color",
    "yellow": "color",
    "orange": "color",
    "silver": "color",
    "gold": "color",
    "spring": "season",
    "summer": "season",
    "fall": "season",
    "winter": "season",
    "all": "season",
    "oversized": "fit",
    "loose": "fit",
    "regular": "fit",
    "slim": "fit",
    "wide": "fit",
    "straight": "fit",
    "bootcut": "fit",
    "flare": "fit",
    "cropped": "fit",
    "semi-wide": "fit",
    "mini": "fit",
    "midi": "fit",
    "maxi": "fit",
    "platform": "fit",
    "solid": "pattern",
    "striped": "pattern",
    "plaid": "pattern",
    "check": "pattern",
    "dot": "pattern",
    "floral": "pattern",
    "logo": "pattern",
    "lettering": "pattern",
    "graphic": "pattern",
    "color block": "pattern",
    "casual": "mood",
    "minimal": "mood",
    "street": "mood",
    "sporty": "mood",
    "formal": "mood",
    "classic": "mood",
    "chic": "mood",
    "romantic": "mood",
    "feminine": "mood",
    "vintage": "mood",
    "retro": "mood",
    "preppy": "mood",
    "outdoor": "mood",
    "utility": "mood",
    "cute": "mood",
}

ITEM_TYPE_VALUES = {
    "반팔티",
    "셔츠",
    "니트",
    "슬랙스",
    "와이드팬츠",
    "스니커즈",
    "바지",
    "상의",
    "신발",
    "원피스/스커트",
}

CATEGORY_VALUES = set(CATEGORY_ALIASES.values())

# refresh_seed 같은 검색 제어값은 요청의 구체성을 판단하는 근거가 아니다.
# preferred_styles도 프로필에서 자동 복사하지 않고, context에 명시됐을 때만 본다.
EXPLICIT_SEARCH_FILTERS = {
    "price_min",
    "price_max",
    "season",
    "style",
    "preferred_styles",
    "sense_of_season",
    "category",
    "item_type",
    "color",
    "mood",
    "gender",
}


# --- 값 정규화 ---

def clean_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"unknown", "none", "null", "nan", "[]"}:
        return None
    return text


def split_tokens(value: Any) -> set[str]:
    text = clean_value(value)
    if not text:
        return set()
    return {token.strip().lower() for token in text.split(",") if clean_value(token)}


def normalize_gender(value: Any) -> str | None:
    text = clean_value(value)
    if not text:
        return None
    return GENDER_ALIASES.get(text.lower(), GENDER_ALIASES.get(text, text))


def normalize_category(value: Any) -> str | None:
    text = clean_value(value)
    if not text:
        return None
    return CATEGORY_ALIASES.get(text.lower(), CATEGORY_ALIASES.get(text, text))


def normalize_season_token(value: Any) -> str | None:
    text = clean_value(value)
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"all-season", "all season", "all seasons", "사계절", "올시즌"}:
        return "all"
    if lowered == "봄":
        return "spring"
    if lowered == "여름":
        return "summer"
    if lowered == "가을":
        return "fall"
    if lowered == "겨울":
        return "winter"
    return lowered


def season_set(value: Any) -> set[str]:
    seasons = {normalize_season_token(token) for token in split_tokens(value)}
    return {season for season in seasons if season}


def expand_query(query: str) -> str:
    expanded = [query]
    lowered = query.lower()

    for keyword, aliases in QUERY_ALIASES.items():
        if keyword.lower() in lowered or keyword in query:
            expanded.extend(aliases)

    # 낱말 단위로 중복을 없앤다. 원본 구현은 리스트 원소 단위로 걸러서, 질의
    # 문장 안에 이미 있는 낱말이 별칭으로 또 붙었다. 같은 낱말이 여러 번 실리면
    # 임베딩에서 그 축만 부풀어 오른다.
    words = (word for chunk in expanded if chunk for word in chunk.split())
    return " ".join(dict.fromkeys(words))


def style_text(item: dict[str, Any]) -> str:
    parts = []
    for field in ("category", "item_type", "color", "material", "fit", "pattern", "mood", "sense_of_season"):
        value = clean_value(item.get(field))
        if value:
            parts.append(value)
    return " ".join(parts)


def limit_words(text: str, max_words: int = MAX_SEARCH_WORDS) -> str:
    return " ".join(text.split()[:max_words])


def clamp_score(score: float) -> float:
    return max(0.0, min(1.0, score))


# --- 질의에서 의도 뽑기 ---


def infer_query_intents(query: str, filters: dict[str, Any] | None = None) -> dict[str, set[str]]:
    """질의가 무엇을 원하는지 필드별로 모은다.

    별칭을 편 뒤에 훑는다. "검정 스트릿"은 확장 전에는 어느 필드에도 걸리지
    않지만, 확장하면 black(color)과 street(mood)로 나뉜다.
    """
    filters = filters or {}
    expanded = expand_query(query)
    intents: dict[str, set[str]] = {
        "category": set(),
        "item_type": set(),
        "color": set(),
        "season": set(),
        "fit": set(),
        "pattern": set(),
        "mood": set(),
    }

    for token in expanded.split():
        category = normalize_category(token)
        if category in CATEGORY_VALUES:
            intents["category"].add(category)
        if token in ITEM_TYPE_VALUES:
            intents["item_type"].add(token)
        field = TOKEN_TO_INTENT_FIELD.get(token)
        if field:
            intents[field].add(token)

    if filters.get("category"):
        intents["category"].add(filters["category"])
    if filters.get("item_type"):
        intents["item_type"].add(filters["item_type"])
    if filters.get("season"):
        intents["season"].update(season_set(filters["season"]))

    return intents


def is_vague_search_request(
    query: str,
    *,
    request_mode: str = "direct",
    has_reference_items: bool = False,
    filters: dict[str, Any] | None = None,
) -> bool:
    """취향으로 검색어를 보완해야 할 만큼 요청이 모호한지 판정한다.

    취향 검색은 명시적으로 허용한 좁은 fallback이다. 사진, 상품 속성, 필터가
    하나라도 있으면 그 의미를 보존하고, ``오늘 뭐 입지``처럼 검색 기준이 없는
    일반 추천에서만 프로필 취향을 임베딩에 더한다.
    """
    if request_mode != "direct" or has_reference_items:
        return False

    filters = filters or {}
    if any(clean_value(filters.get(key)) is not None for key in EXPLICIT_SEARCH_FILTERS):
        return False

    if any(infer_query_intents(query).values()):
        return False

    compact = re.sub(r"[^0-9a-zA-Z가-힣]+", "", query).casefold()
    if not compact:
        return True

    korean_patterns = (
        r"(?:오늘)?(?:뭐|무엇)(?:을|를)?(?:입지|입을까|입으면좋을까|입어야할까)",
        r"(?:나|저)(?:한테|에게)어울리는(?:옷|거|것)?(?:좀)?(?:추천|추천해줘|추천해주세요|골라줘)?",
        r"(?:오늘의?|데일리)?(?:옷|의류|상품|코디)?(?:좀)?(?:추천|추천해줘|추천해주세요|골라줘)",
        r"아무거나(?:좀)?(?:추천|추천해줘|추천해주세요|골라줘)",
    )
    if any(re.fullmatch(pattern, compact) for pattern in korean_patterns):
        return True

    return compact in {
        "whatshouldiwear",
        "recommendmeoutfit",
        "recommendsomething",
        "outfitrecommendation",
    }


# --- 임베딩에 넣을 문장 ---


def build_search_text(
    query: str,
    vlm_items: list[dict] | None = None,
    preferred_styles: list[str] | str | None = None,
    use_preference_search: bool = False,
    request_mode: str = "direct",
) -> str:
    """벡터 검색에 실제로 임베딩할 문장.

    기본 검색 의미는 사용자 요청과 사진 참고 정보만 만든다. 옷장 정보는 이
    단계에 넣지 않고 ranker에서만 사용한다. 취향 역시 호출부가 모호한 일반
    추천으로 판정해 ``use_preference_search``를 켠 경우에만 보완 신호로 쓴다.
    """
    parts = [expand_query(query)]

    # In coordination mode the Query Refiner has already produced a candidate-
    # focused query. Appending raw reference attributes again would make the
    # embedding search for the same color/category instead of a complementary item.
    if request_mode != "coordination":
        for item in (vlm_items or [])[:3]:
            text_value = style_text(item)
            if text_value:
                parts.append(text_value)

    if use_preference_search:
        if isinstance(preferred_styles, str):
            preferred_styles = [preferred_styles]
        parts.extend(str(style) for style in (preferred_styles or []) if clean_value(style))

    return limit_words(" ".join(parts))


# --- 메타데이터 점수 ---


def metadata_score(
    item: dict[str, Any],
    intents: dict[str, set[str]],
    filters: dict[str, Any] | None = None,
) -> float:
    """질의 의도와 상품 메타데이터가 얼마나 맞는지. 0~1.

    벡터 유사도만으로는 "검정"을 요청해도 흰 옷이 위에 온다. 문서 전체가
    비슷하면 색 한 낱말의 차이는 묻히기 때문이다. 맞아야 하는 필드에
    명시적으로 점수를 준다.
    """
    filters = filters or {}
    score = 0.0

    category = clean_value(item.get("category"))
    if category and category in intents["category"]:
        score += 0.20

    item_type = clean_value(item.get("item_type") or item.get("label"))
    if item_type and item_type in intents["item_type"]:
        score += 0.20

    # pgvector는 season 컬럼을 sense_of_season으로 실어 보내고, chroma는
    # season_norm으로 보낸다. 어느 쪽이 와도 읽는다.
    item_seasons = season_set(
        item.get("sense_of_season") or item.get("season_norm") or item.get("season")
    )
    wanted_seasons = intents["season"] | season_set(filters.get("season"))
    if wanted_seasons and ("all" in item_seasons or item_seasons & wanted_seasons):
        score += 0.15

    colors = split_tokens(item.get("color"))
    if colors & intents["color"]:
        score += 0.15

    fits = split_tokens(item.get("fit"))
    if fits & intents["fit"]:
        score += 0.10

    moods = split_tokens(item.get("mood"))
    if moods & intents["mood"]:
        score += 0.10

    return clamp_score(score)


def final_score(similarity: float, meta_score: float) -> float:
    return clamp_score((similarity * SIMILARITY_WEIGHT) + (meta_score * METADATA_WEIGHT))
