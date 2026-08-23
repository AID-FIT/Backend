from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = str(BASE_DIR / "data" / "chromadb_final")
COLLECTION_NAME = "musinsa"
MODEL_NAME = "jhgan/ko-sroberta-multitask"

DEFAULT_TOP_K = 10
SEARCH_MULTIPLIER = 10
MAX_SEARCH_WORDS = 120
SIMILARITY_WEIGHT = 0.75
METADATA_WEIGHT = 0.25


class RAGRequest(BaseModel):
    user_id: str
    query: str
    retrieval_target: Literal["closet", "musinsa", "hybrid"]
    user_profile: Optional[dict[str, Any]] = None
    vlm_items: list[dict[str, Any]] = Field(default_factory=list)
    closet_items: list[dict[str, Any]] = Field(default_factory=list)
    use_closet_style: bool = True
    filters: Optional[dict[str, Any]] = None
    top_k: int = DEFAULT_TOP_K


class RAGItem(BaseModel):
    item_id: Optional[str] = None
    source: Literal["closet", "musinsa"]
    name: Optional[str] = None
    brand: Optional[str] = None
    price: Optional[int] = None
    category: Optional[str] = None
    label: Optional[str] = None
    gender: Optional[str] = None
    image_url: str
    product_url: Optional[str] = None
    color: Optional[str] = None
    material: Optional[str] = None
    fit: Optional[str] = None
    pattern: Optional[str] = None
    mood: Optional[str] = None
    sense_of_season: Optional[str] = None
    similarity_score: Optional[float] = None
    metadata_score: Optional[float] = None
    final_score: Optional[float] = None


class RAGResponse(BaseModel):
    items: list[RAGItem] = Field(default_factory=list)
    message: Optional[str] = None


class RAGSearchError(RuntimeError):
    code = "RAG_SEARCH_FAILED"
    retryable = True
    source = "rag"


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

CATEGORY_VALUES = set(CATEGORY_ALIASES.values())
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

TARGET_CATEGORY_KEYWORDS = {
    "바지": (
        "바지",
        "하의",
        "팬츠",
        "슬랙스",
        "와이드팬츠",
        "와이드 팬츠",
        "청바지",
        "데님팬츠",
        "조거팬츠",
        "숏팬츠",
        "카고팬츠",
    ),
    "상의": (
        "상의",
        "반팔",
        "반팔티",
        "티셔츠",
        "셔츠",
        "니트",
        "맨투맨",
        "후드티",
        "블라우스",
    ),
    "아우터": ("아우터", "자켓", "재킷", "코트", "패딩", "바람막이", "후드집업"),
    "모자": ("모자", "볼캡", "캡", "비니", "버킷햇", "선캡"),
    "신발": ("신발", "스니커즈", "운동화", "로퍼", "부츠", "샌들", "슬리퍼", "구두"),
    "가방": ("가방", "백팩", "숄더백", "크로스백", "토트백", "파우치", "지갑"),
    "원피스/스커트": ("원피스", "스커트", "미니원피스", "미니 원피스", "미니스커트", "미니 스커트"),
}


def clean_value(value: Any) -> Optional[str]:
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


def normalize_gender(value: Any) -> Optional[str]:
    text = clean_value(value)
    if not text:
        return None
    return GENDER_ALIASES.get(text.lower(), GENDER_ALIASES.get(text, text))


def normalize_category(value: Any) -> Optional[str]:
    text = clean_value(value)
    if not text:
        return None
    return CATEGORY_ALIASES.get(text.lower(), CATEGORY_ALIASES.get(text, text))


def normalize_season_token(value: Any) -> Optional[str]:
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


def parse_price(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize_filters(filters: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not filters:
        return {}

    normalized: dict[str, Any] = {}
    gender = normalize_gender(filters.get("gender"))
    if gender:
        normalized["gender"] = gender

    category = normalize_category(filters.get("category"))
    if category:
        normalized["category"] = category

    item_type = clean_value(filters.get("item_type"))
    if item_type:
        normalized["item_type"] = item_type

    price_max = filters.get("price_max", filters.get("max_price"))
    price_min = filters.get("price_min", filters.get("min_price"))
    parsed_max = parse_price(price_max)
    parsed_min = parse_price(price_min)
    if parsed_max is not None:
        normalized["price_max"] = parsed_max
    if parsed_min is not None:
        normalized["price_min"] = parsed_min

    season = filters.get("season", filters.get("sense_of_season"))
    season_tokens = season_set(season)
    if season_tokens:
        normalized["season"] = season_tokens

    return normalized


def expand_query(query: str) -> str:
    expanded = [query]
    lowered = query.lower()

    for keyword, aliases in QUERY_ALIASES.items():
        if keyword.lower() in lowered or keyword in query:
            expanded.extend(aliases)

    return " ".join(dict.fromkeys(token for token in expanded if token))


def infer_query_intents(query: str, filters: dict[str, Any]) -> dict[str, set[str]]:
    expanded = expand_query(query)
    intents = {
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
        intents["season"].update(filters["season"])

    return intents


def style_text(item: dict[str, Any]) -> str:
    parts = []
    for field in ("category", "item_type", "color", "material", "fit", "pattern", "mood", "sense_of_season"):
        value = clean_value(item.get(field))
        if value:
            parts.append(value)
    return " ".join(parts)


def limit_words(text: str, max_words: int = MAX_SEARCH_WORDS) -> str:
    return " ".join(text.split()[:max_words])


def build_search_text(request: RAGRequest, filters: dict[str, Any]) -> str:
    parts = [expand_query(request.query)]

    for item in request.vlm_items[:3]:
        text = style_text(item)
        if text:
            parts.append(text)

    if request.use_closet_style:
        for item in request.closet_items[:5]:
            text = style_text(item)
            if text:
                parts.append(text)

    preferred_styles = (request.user_profile or {}).get("preferred_styles") or []
    if isinstance(preferred_styles, str):
        preferred_styles = [preferred_styles]
    parts.extend(str(style) for style in preferred_styles if clean_value(style))

    return limit_words(" ".join(parts))


def build_chroma_where(filters: dict[str, Any]) -> Optional[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []

    if filters.get("gender"):
        conditions.append({"gender": {"$eq": filters["gender"]}})
    if filters.get("category"):
        conditions.append({"category": {"$eq": filters["category"]}})
    if filters.get("price_min") is not None:
        conditions.append({"price": {"$gte": filters["price_min"]}})
    if filters.get("price_max") is not None:
        conditions.append({"price": {"$lte": filters["price_max"]}})

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def infer_target_category(query: str) -> Optional[str]:
    matches = [
        (category, keyword)
        for category, keywords in TARGET_CATEGORY_KEYWORDS.items()
        for keyword in keywords
        if keyword.lower() in query.lower() or keyword in query
    ]
    if not matches:
        return None

    # In styling queries, the target usually appears closest to "추천".
    recommend_pos = query.find("추천")
    if recommend_pos >= 0:
        before_recommend = [
            (category, keyword, query.rfind(keyword, 0, recommend_pos))
            for category, keyword in matches
            if query.rfind(keyword, 0, recommend_pos) >= 0
        ]
        if before_recommend:
            before_recommend.sort(key=lambda row: row[2], reverse=True)
            return before_recommend[0][0]

    matches.sort(key=lambda row: query.lower().rfind(row[1].lower()), reverse=True)
    return matches[0][0]


def build_effective_filters(request: RAGRequest) -> dict[str, Any]:
    filters = normalize_filters(request.filters)
    if not filters.get("category"):
        target_category = infer_target_category(request.query)
        if target_category:
            filters["category"] = target_category
    return filters


def get_collection() -> Collection:
    embedding_function = SentenceTransformerEmbeddingFunction(
        model_name=MODEL_NAME,
        local_files_only=True,
    )
    client = chromadb.PersistentClient(path=DB_PATH)
    return client.get_collection(COLLECTION_NAME, embedding_function=embedding_function)


def clamp_score(score: float) -> float:
    return max(0.0, min(1.0, score))


def calculate_final_score(similarity_score: float, metadata_score: float) -> float:
    return clamp_score((similarity_score * SIMILARITY_WEIGHT) + (metadata_score * METADATA_WEIGHT))


def calculate_metadata_score(
    metadata: dict[str, Any],
    intents: dict[str, set[str]],
    filters: dict[str, Any],
    request: RAGRequest,
) -> float:
    score = 0.0

    category = clean_value(metadata.get("category"))
    if category and category in intents["category"]:
        score += 0.20

    item_type = clean_value(metadata.get("item_type"))
    if item_type and item_type in intents["item_type"]:
        score += 0.20

    item_seasons = season_set(metadata.get("season_norm"))
    wanted_seasons = intents["season"] | set(filters.get("season") or set())
    if wanted_seasons and ("all" in item_seasons or item_seasons & wanted_seasons):
        score += 0.15

    colors = split_tokens(metadata.get("color"))
    if colors & intents["color"]:
        score += 0.15

    fits = split_tokens(metadata.get("fit"))
    if fits & intents["fit"]:
        score += 0.10

    moods = split_tokens(metadata.get("mood"))
    if moods & intents["mood"]:
        score += 0.10

    preferred_styles = (request.user_profile or {}).get("preferred_styles") or []
    if isinstance(preferred_styles, str):
        preferred_styles = [preferred_styles]
    preferred_tokens = {str(style).lower() for style in preferred_styles if clean_value(style)}
    if moods & preferred_tokens:
        score += 0.10

    if request.use_closet_style and request.closet_items:
        closet_matches = 0
        for closet_item in request.closet_items[:5]:
            for field in ("color", "fit", "pattern", "mood"):
                if split_tokens(metadata.get(field)) & split_tokens(closet_item.get(field)):
                    closet_matches += 1
                    break
        if closet_matches:
            score += min(0.10, closet_matches * 0.03)

    return clamp_score(score)


def build_musinsa_rag_item(
    metadata: dict[str, Any],
    distance: float,
    metadata_score: float,
) -> RAGItem:
    similarity_score = clamp_score(1.0 - float(distance))
    final_score = calculate_final_score(similarity_score, metadata_score)
    return RAGItem(
        item_id=clean_value(metadata.get("item_id")),
        source="musinsa",
        name=clean_value(metadata.get("name")),
        brand=clean_value(metadata.get("brand")),
        price=parse_price(metadata.get("price")),
        category=clean_value(metadata.get("category")),
        label=clean_value(metadata.get("label")),
        gender=clean_value(metadata.get("gender")),
        image_url=clean_value(metadata.get("thumbnail_url")) or "",
        product_url=clean_value(metadata.get("product_url")),
        color=clean_value(metadata.get("color")),
        material=clean_value(metadata.get("material")),
        fit=clean_value(metadata.get("fit")),
        pattern=clean_value(metadata.get("pattern")),
        mood=clean_value(metadata.get("mood")),
        sense_of_season=clean_value(metadata.get("sense_of_season")),
        similarity_score=round(similarity_score, 4),
        metadata_score=round(metadata_score, 4),
        final_score=round(final_score, 4),
    )


def build_closet_rag_item(item: dict[str, Any]) -> RAGItem:
    return RAGItem(
        item_id=clean_value(item.get("closet_item_id") or item.get("item_id") or item.get("id")),
        source="closet",
        name=clean_value(item.get("name") or item.get("item_name")),
        brand=clean_value(item.get("brand")),
        price=parse_price(item.get("price")),
        category=normalize_category(item.get("category")) or clean_value(item.get("category")),
        label=clean_value(item.get("label")),
        gender=clean_value(item.get("gender")),
        image_url=clean_value(item.get("image_url") or item.get("thumbnail_url")) or "",
        product_url=clean_value(item.get("product_url")),
        color=clean_value(item.get("color")),
        material=clean_value(item.get("material")),
        fit=clean_value(item.get("fit")),
        pattern=clean_value(item.get("pattern")),
        mood=clean_value(item.get("mood")),
        sense_of_season=clean_value(item.get("sense_of_season")),
        similarity_score=None,
        metadata_score=None,
        final_score=None,
    )


def deduplicate_musinsa(items: list[RAGItem]) -> list[RAGItem]:
    best_by_url: dict[str, RAGItem] = {}
    no_url: list[RAGItem] = []

    for item in items:
        if item.source != "musinsa" or not item.product_url:
            no_url.append(item)
            continue
        existing = best_by_url.get(item.product_url)
        if existing is None or (item.final_score or 0.0) > (existing.final_score or 0.0):
            best_by_url[item.product_url] = item

    return [*best_by_url.values(), *no_url]


def search_musinsa(
    request: RAGRequest,
    collection: Optional[Collection] = None,
    fallback: bool = False,
) -> list[RAGItem]:
    filters = build_effective_filters(request)
    if fallback:
        filters = dict(filters)
        filters.pop("season", None)
        if filters.get("price_max") is not None:
            filters["price_max"] = int(filters["price_max"] * 1.3)

    intents = infer_query_intents(request.query, filters)
    search_text = build_search_text(request, filters)
    where = build_chroma_where(filters)
    collection = collection or get_collection()
    n_results = max(request.top_k * SEARCH_MULTIPLIER, request.top_k)
    if fallback:
        n_results *= 2

    result = collection.query(
        query_texts=[search_text],
        n_results=n_results,
        where=where,
        include=["metadatas", "distances"],
    )
    metadatas = result.get("metadatas", [[]])[0] or []
    distances = result.get("distances", [[]])[0] or []

    items: list[RAGItem] = []
    for metadata, distance in zip(metadatas, distances):
        metadata_score = calculate_metadata_score(metadata, intents, filters, request)
        items.append(build_musinsa_rag_item(metadata, float(distance), metadata_score))

    items = deduplicate_musinsa(items)
    items.sort(key=lambda item: item.final_score or 0.0, reverse=True)
    return items[: request.top_k]


def search_closet(request: RAGRequest) -> list[RAGItem]:
    return [build_closet_rag_item(item) for item in request.closet_items[: request.top_k]]


def search_hybrid(request: RAGRequest) -> list[RAGItem]:
    closet_items = search_closet(request)
    musinsa_limit = max(request.top_k - len(closet_items), 0)
    musinsa_request = request.model_copy(update={"top_k": max(musinsa_limit, request.top_k)})
    musinsa_items = search_musinsa(musinsa_request)

    # Hybrid should expose both sources to Agent. Keep musinsa ranked by score,
    # then append selected closet context items within the requested top_k budget.
    return [*musinsa_items[:musinsa_limit], *closet_items][: request.top_k]


def search(request: RAGRequest | dict[str, Any]) -> RAGResponse:
    try:
        parsed = request if isinstance(request, RAGRequest) else RAGRequest(**request)
        if not parsed.query.strip():
            return RAGResponse(items=[], message="검색 결과가 없습니다.")

        if parsed.retrieval_target == "closet":
            items = search_closet(parsed)
        elif parsed.retrieval_target == "musinsa":
            items = search_musinsa(parsed)
            if not items:
                items = search_musinsa(parsed, fallback=True)
        else:
            items = search_hybrid(parsed)
            if not items:
                items = search_musinsa(parsed, fallback=True)

        if not items:
            return RAGResponse(items=[], message="검색 결과가 없습니다.")
        return RAGResponse(items=items, message="success")
    except Exception as exc:
        raise RAGSearchError("추천 상품 검색 중 오류가 발생했습니다.") from exc


def error_response(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, RAGSearchError):
        return {
            "status": "error",
            "error": {
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "source": exc.source,
            },
        }
    raise exc


if __name__ == "__main__":
    demos = [
        {
            "user_id": "demo",
            "query": "검정색 오버핏 셔츠 추천해줘",
            "retrieval_target": "musinsa",
            "filters": {"gender": "men", "category": "상의"},
            "top_k": 5,
        },
        {
            "user_id": "demo",
            "query": "봄 가을에 입기 좋은 와이드팬츠",
            "retrieval_target": "musinsa",
            "filters": {"category": "하의", "price_max": 50000, "season": "spring"},
            "top_k": 5,
        },
        {
            "user_id": "demo",
            "query": "화이트 니트랑 어울리는 미니멀한 바지 추천",
            "retrieval_target": "hybrid",
            "vlm_items": [
                {
                    "category": "top",
                    "color": "white",
                    "material": "knit",
                    "fit": "oversized",
                    "mood": "minimal",
                    "sense_of_season": "spring",
                }
            ],
            "closet_items": [
                {
                    "closet_item_id": "closet_001",
                    "category": "bag",
                    "color": "black",
                    "material": "pvc",
                    "fit": "none",
                    "pattern": "graphic",
                    "mood": "street",
                    "sense_of_season": "summer",
                    "image_url": "https://example.com/closet_001.jpg",
                }
            ],
            "use_closet_style": True,
            "filters": {"price_max": 80000, "season": "spring"},
            "top_k": 10,
        },
    ]

    for demo in demos:
        response = search(demo)
        print(response.model_dump_json(indent=2, ensure_ascii=False))


