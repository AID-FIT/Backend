"""성별 표기를 카탈로그가 쓰는 정규형으로 맞춘다.

카탈로그(`product_vectors.gender`)와 옷장(`closet_items.gender`)은 men/women/unisex
세 값만 쓴다. VLM 응답과 사용자 입력이 각자 다른 표기를 보내오므로, 두 경로가
같은 표를 보게 여기 한 벌만 둔다.
"""

CANONICAL_GENDERS = ("men", "women", "unisex")

GENDER_SYNONYMS = {
    "male": "men",
    "man": "men",
    "men's": "men",
    "mens": "men",
    "남성": "men",
    "남자": "men",
    "female": "women",
    "woman": "women",
    "women's": "women",
    "womens": "women",
    "여성": "women",
    "여자": "women",
    "uni": "unisex",
    "both": "unisex",
    "공용": "unisex",
    "남녀공용": "unisex",
}


def normalize_gender(value: object) -> str | None:
    """men/women/unisex 중 하나로 바꾼다. 비었으면 None, 못 알아보면 ValueError.

    조용히 버리지 않는다. 알 수 없는 값을 None으로 삼키면 사용자가 성별을
    골랐는데도 필터가 안 걸리는 상태를 아무도 눈치채지 못한다.
    """
    if value is None:
        return None

    text = str(value).strip().lower()
    if not text:
        return None

    normalized = GENDER_SYNONYMS.get(text, text)
    if normalized not in CANONICAL_GENDERS:
        raise ValueError(f"지원하지 않는 성별 값입니다: {value!r}")
    return normalized
