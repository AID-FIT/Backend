from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
INPUT_DIR = BASE_DIR / "data" / "final_vlm_dataset"
OUTPUT_DIR = BASE_DIR / "data" / "preprocessed_vlm_dataset"

REQUIRED_FIELDS = (
    "product_id",
    "name",
    "brand",
    "price",
    "category",
    "label",
    "gender",
    "thumbnail_url",
    "product_url",
    "item_type",
    "color",
    "material",
    "fit",
    "pattern",
    "mood",
    "sense_of_season",
)

MULTI_VALUE_FIELDS = (
    "color",
    "material",
    "fit",
    "pattern",
    "mood",
    "sense_of_season",
)

UNKNOWN_VALUES = {"", "unknown", "none", "null", "nan", "[]"}
SEASON_ORDER = ("spring", "summer", "fall", "winter")

ALIASES: dict[str, dict[str, list[str]]] = {
    "gender": {
        "men": ["남자", "남성"],
        "women": ["여자", "여성"],
    },
    "season": {
        "spring": ["봄", "간절기"],
        "summer": ["여름", "시원한", "얇은"],
        "fall": ["가을", "간절기"],
        "winter": ["겨울", "따뜻한", "두꺼운"],
        "all": ["사계절", "올시즌", "무난한"],
    },
    "color": {
        "black": ["검정", "검은색", "블랙", "까만색", "어두운색", "다크톤", "무채색"],
        "white": ["흰색", "하얀색", "화이트", "밝은색", "무채색"],
        "gray": ["회색", "그레이", "무채색"],
        "charcoal": ["차콜", "진회색", "어두운색", "다크톤", "무채색"],
        "silver": ["실버", "은색"],
        "ivory": ["아이보리", "오프화이트", "크림색", "밝은색"],
        "cream": ["크림", "크림색", "밝은색"],
        "beige": ["베이지", "연베이지", "밝은색", "뉴트럴톤"],
        "brown": ["브라운", "갈색", "어두운색"],
        "camel": ["카멜", "브라운", "갈색"],
        "khaki": ["카키", "올리브", "초록계열"],
        "blue": ["파랑", "파란색", "블루", "푸른색", "파란계열"],
        "navy": ["네이비", "남색", "어두운색", "다크톤", "파란계열"],
        "sky blue": ["하늘색", "스카이블루", "파란계열"],
        "denim blue": ["데님블루", "청색", "파란계열"],
        "green": ["초록", "초록색", "그린", "초록계열"],
        "olive": ["올리브", "카키", "초록계열"],
        "mint": ["민트", "민트색", "초록계열"],
        "red": ["빨강", "빨간색", "레드", "붉은색", "붉은계열"],
        "pink": ["핑크", "분홍", "분홍색", "붉은계열"],
        "burgundy": ["버건디", "와인", "와인색", "자주색", "붉은계열"],
        "purple": ["보라", "보라색", "퍼플"],
        "yellow": ["노랑", "노란색", "옐로우"],
        "orange": ["오렌지", "주황", "주황색"],
        "gold": ["골드", "금색"],
        "multi": ["멀티", "다색", "여러색"],
    },
    "material": {
        "cotton": ["면", "코튼"],
        "polyester": ["폴리에스터"],
        "nylon": ["나일론"],
        "leather": ["가죽", "레더"],
        "faux leather": ["인조가죽", "페이크레더"],
        "vegan leather": ["비건레더", "인조가죽"],
        "suede": ["스웨이드"],
        "denim": ["데님", "청"],
        "linen": ["린넨", "마"],
        "wool": ["울", "모", "따뜻한"],
        "knit": ["니트"],
        "acrylic": ["아크릴"],
        "rayon": ["레이온"],
        "spandex": ["스판", "신축성"],
        "mesh": ["메쉬", "통기성", "시원한"],
        "canvas": ["캔버스"],
        "rubber": ["고무", "러버"],
        "fleece": ["플리스", "따뜻한"],
        "corduroy": ["코듀로이", "골덴"],
        "tweed": ["트위드"],
        "satin": ["새틴"],
        "lace": ["레이스"],
        "jersey": ["저지"],
    },
    "fit": {
        "regular": ["레귤러", "기본핏", "정핏"],
        "oversized": ["오버핏", "오버사이즈", "박시한", "넉넉한"],
        "loose": ["루즈핏", "여유있는", "넉넉한"],
        "slim": ["슬림", "슬림핏", "붙는"],
        "wide": ["와이드", "넓은"],
        "straight": ["스트레이트", "일자핏"],
        "tapered": ["테이퍼드"],
        "bootcut": ["부츠컷"],
        "flare": ["플레어"],
        "cropped": ["크롭", "짧은기장"],
        "semi-wide": ["세미와이드"],
        "a-line": ["에이라인"],
        "h-line": ["에이치라인"],
        "mini": ["미니", "짧은기장"],
        "midi": ["미디", "중간기장"],
        "maxi": ["맥시", "긴기장"],
        "adjustable": ["조절가능"],
        "structured": ["각진", "탄탄한"],
        "unstructured": ["부드러운", "자연스러운"],
        "platform": ["플랫폼", "키높이"],
        "low-top": ["로우탑"],
        "mid-top": ["미드탑"],
        "high-top": ["하이탑"],
    },
    "pattern": {
        "solid": ["무지", "단색"],
        "striped": ["스트라이프", "줄무늬"],
        "plaid": ["체크"],
        "check": ["체크"],
        "gingham": ["깅엄체크", "체크"],
        "dot": ["도트", "물방울"],
        "floral": ["플로럴", "꽃무늬"],
        "logo": ["로고"],
        "lettering": ["레터링"],
        "graphic": ["그래픽", "프린팅"],
        "color block": ["컬러블록", "배색"],
        "camouflage": ["카모", "카무플라주", "밀리터리"],
        "leopard": ["레오파드", "호피"],
        "embroidered": ["자수"],
        "ribbed": ["골지"],
        "cable knit": ["케이블니트"],
        "quilted": ["퀼팅"],
        "washed": ["워싱"],
        "distressed": ["디스트로이드", "빈티지"],
        "lace": ["레이스"],
        "mesh": ["메쉬"],
        "sheer": ["시스루"],
        "textured": ["텍스처", "조직감"],
    },
    "mood": {
        "casual": ["캐주얼", "데일리", "편한", "편안한"],
        "minimal": ["미니멀", "깔끔한", "심플한", "단정한"],
        "street": ["스트릿", "힙한", "개성있는"],
        "sporty": ["스포티", "스포츠", "활동적인"],
        "formal": ["포멀", "격식있는", "출근룩", "오피스룩"],
        "classic": ["클래식", "기본", "베이직"],
        "chic": ["시크", "세련된"],
        "romantic": ["로맨틱"],
        "feminine": ["페미닌", "여성스러운"],
        "vintage": ["빈티지"],
        "retro": ["레트로"],
        "preppy": ["프레피"],
        "outdoor": ["아웃도어"],
        "utility": ["유틸리티", "실용적인"],
        "edgy": ["에지있는", "개성있는"],
        "cute": ["귀여운", "러블리한"],
    },
}


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).strip())


def is_unknown(value: Any) -> bool:
    return clean_text(value).lower() in UNKNOWN_VALUES


def split_tokens(value: Any) -> list[str]:
    if value is None:
        return []

    tokens: list[str] = []
    for raw_token in str(value).split(","):
        token = clean_text(raw_token).lower()
        if token in UNKNOWN_VALUES or token in tokens:
            continue
        tokens.append(token)
    return tokens


def normalize_seasons(value: Any) -> list[str]:
    tokens: list[str] = []
    for token in split_tokens(value):
        if token in {"all-season", "all season", "all seasons"}:
            return ["all"]
        if token == "all":
            return ["all"]
        if token in SEASON_ORDER and token not in tokens:
            tokens.append(token)

    if set(tokens) == set(SEASON_ORDER):
        return ["all"]
    if not tokens:
        return ["unknown"]
    return [season for season in SEASON_ORDER if season in tokens]


def join_tokens(tokens: list[str]) -> str:
    return ", ".join(tokens) if tokens else "unknown"


def expand_tokens(kind: str, tokens: list[str]) -> str:
    expanded: list[str] = []
    for token in tokens:
        if token in UNKNOWN_VALUES or token == "unknown":
            continue
        expanded.append(token)
        expanded.extend(ALIASES.get(kind, {}).get(token, []))
    return " ".join(dict.fromkeys(expanded))


def make_item_id(item: dict[str, Any]) -> str:
    return f"musinsa_{clean_text(item.get('product_id'))}_{clean_text(item.get('gender'))}_{clean_text(item.get('category'))}"


def build_search_document(item: dict[str, Any], season_tokens: list[str]) -> str:
    color_tokens = split_tokens(item.get("color"))
    material_tokens = split_tokens(item.get("material"))
    fit_tokens = split_tokens(item.get("fit"))
    pattern_tokens = split_tokens(item.get("pattern"))
    mood_tokens = split_tokens(item.get("mood"))
    gender = clean_text(item.get("gender"))

    lines = [
        f"상품명: {clean_text(item.get('name'))}",
        f"브랜드: {clean_text(item.get('brand'))}",
        f"성별: {expand_tokens('gender', [gender])}",
        f"카테고리: {clean_text(item.get('category'))}",
        f"세부종류: {clean_text(item.get('item_type'))}",
        f"색상: {expand_tokens('color', color_tokens)}",
        f"소재: {expand_tokens('material', material_tokens)}",
        f"핏: {expand_tokens('fit', fit_tokens)}",
        f"패턴: {expand_tokens('pattern', pattern_tokens)}",
        f"무드: {expand_tokens('mood', mood_tokens)}",
        f"계절: {expand_tokens('season', season_tokens)}",
    ]
    return "\n".join(line for line in lines if not line.endswith(": "))


def preprocess_item(
    item: dict[str, Any],
    source_file: str,
    stats: dict[str, int],
) -> dict[str, Any]:
    # Keep original VLM fields byte-for-byte for interface responses.
    # Normalization is applied only to derived fields such as item_id and search_document.
    output = dict(item)

    for field in REQUIRED_FIELDS:
        if field not in item:
            stats["missing_fields"] += 1
        elif item[field] is None or (isinstance(item[field], str) and not clean_text(item[field])):
            stats["empty_values"] += 1

    season_tokens = normalize_seasons(item.get("sense_of_season"))

    output["item_id"] = make_item_id(output)
    output["season_norm"] = join_tokens(season_tokens)
    output["search_document"] = build_search_document(output, season_tokens)
    output["source_file"] = source_file
    return output


def load_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: JSON root must be a list")
    return data


def save_json(path: Path, data: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main() -> None:
    if not INPUT_DIR.exists():
        raise FileNotFoundError(f"Input directory not found: {INPUT_DIR}")

    OUTPUT_DIR.mkdir(exist_ok=True)

    total_input = 0
    total_output = 0
    all_item_ids: list[str] = []
    print(f"Input: {INPUT_DIR}")
    print(f"Output: {OUTPUT_DIR}")

    for input_path in sorted(INPUT_DIR.glob("*.json")):
        stats = {"missing_fields": 0, "empty_values": 0}
        items = load_json(input_path)
        processed = [
            preprocess_item(item, input_path.name, stats)
            for item in items
        ]

        output_path = OUTPUT_DIR / input_path.name
        save_json(output_path, processed)

        item_ids = [item["item_id"] for item in processed]
        duplicate_count = len(item_ids) - len(set(item_ids))
        all_item_ids.extend(item_ids)

        total_input += len(items)
        total_output += len(processed)

        print(
            f"{input_path.name}: input={len(items)} output={len(processed)} "
            f"missing_fields={stats['missing_fields']} empty_values={stats['empty_values']} "
            f"duplicate_item_ids={duplicate_count}"
        )

    total_duplicate_ids = len(all_item_ids) - len(set(all_item_ids))
    print("-" * 80)
    print(f"total_input={total_input}")
    print(f"total_output={total_output}")
    print(f"total_duplicate_item_ids={total_duplicate_ids}")


if __name__ == "__main__":
    main()


