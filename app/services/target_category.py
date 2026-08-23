"""질의에서 **찾는 옷**의 카테고리를 뽑는다.

코디 추천에서 사진 속 옷은 참고 대상이고, 사용자가 달라는 옷은 따로다.
"이 바지에 어울리는 상의 추천해줘"에서 검색해야 할 것은 상의다.
이 구분이 없으면 벡터 검색이 질의와 비슷한 옷(=또 바지)을 돌려준다.
"""

TARGET_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "바지": (
        "바지", "하의", "팬츠", "슬랙스", "와이드팬츠", "와이드 팬츠",
        "청바지", "데님팬츠", "조거팬츠", "숏팬츠", "카고팬츠",
    ),
    "상의": (
        "상의", "반팔", "반팔티", "티셔츠", "셔츠", "니트",
        "맨투맨", "후드티", "블라우스",
    ),
    "아우터": ("아우터", "자켓", "재킷", "코트", "패딩", "바람막이", "후드집업"),
    "모자": ("모자", "볼캡", "캡", "비니", "버킷햇", "선캡"),
    "신발": ("신발", "스니커즈", "운동화", "로퍼", "부츠", "샌들", "슬리퍼", "구두"),
    "가방": ("가방", "백팩", "숄더백", "크로스백", "토트백", "파우치", "지갑"),
    "원피스/스커트": (
        "원피스", "스커트", "미니원피스", "미니 원피스", "미니스커트", "미니 스커트",
    ),
}

# 이 말들 뒤에 오는 카테고리는 "가진 옷"이지 "찾는 옷"이 아니다.
REFERENCE_MARKERS = ("어울리", "매치", "코디", "함께", "같이", "맞는", "어떤")


def infer_target_category(query: str) -> str | None:
    """찾는 옷의 카테고리. 판단할 수 없으면 None."""
    if not query:
        return None

    lowered = query.lower()
    matches = [
        (category, keyword)
        for category, keywords in TARGET_CATEGORY_KEYWORDS.items()
        for keyword in keywords
        if keyword.lower() in lowered
    ]
    if not matches:
        return None

    # 스타일링 질의에서 찾는 옷은 보통 "추천" 바로 앞에 온다.
    # "바지에 어울리는 상의 추천해줘" → 추천 앞의 마지막 카테고리는 상의.
    recommend_pos = query.find("추천")
    if recommend_pos >= 0:
        before_recommend = [
            (category, query.rfind(keyword, 0, recommend_pos))
            for category, keyword in matches
            if query.rfind(keyword, 0, recommend_pos) >= 0
        ]
        if before_recommend:
            before_recommend.sort(key=lambda row: row[1], reverse=True)
            return before_recommend[0][0]

    # "추천"이 없으면 마지막에 언급된 카테고리를 택한다.
    matches.sort(key=lambda row: lowered.rfind(row[1].lower()), reverse=True)
    return matches[0][0]


def query_names_a_category(query: str) -> bool:
    """질의가 찾는 옷의 카테고리를 직접 말했는가.

    말했다면 사진에서 읽은 카테고리를 검색 필터로 쓰면 안 된다.
    사진 속 옷은 참고 대상이고, 찾는 옷은 질의가 정한다.

    두 값을 직접 비교하지 않는 이유가 있다. VLM은 영어 카테고리("top")를,
    질의 키워드는 한국어("상의")를 쓴다. 같은 옷이어도 문자열이 다르다.
    """
    return infer_target_category(query) is not None
