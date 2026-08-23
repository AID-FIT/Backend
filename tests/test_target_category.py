import pytest

from app.services.target_category import (
    infer_target_category,
    query_names_a_category,
)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # 스타일링 질의에서 찾는 옷은 "추천" 바로 앞에 온다.
        ("이 바지에 어울리는 상의 추천해줘", "상의"),
        ("검은 재킷이랑 매치할 바지 추천해줘", "바지"),
        ("이 셔츠에 신을 신발 추천해줘", "신발"),
        ("코트에 어울리는 가방 추천", "가방"),
        # "추천"이 없으면 마지막에 언급된 카테고리.
        ("반팔 티셔츠 찾고 있어", "상의"),
        ("와이드 슬랙스 보여줘", "바지"),
        # 카테고리를 말하지 않으면 판단하지 않는다.
        ("데일리로 입기 좋은 거 추천해줘", None),
        ("", None),
    ],
)
def test_infer_target_category(query: str, expected: str | None) -> None:
    assert infer_target_category(query) == expected


def test_query_naming_a_target_category_is_detected() -> None:
    # 바지 사진을 올리고 상의를 달라고 했다. 사진의 바지로 검색을 가두면 안 된다.
    assert query_names_a_category("이 바지에 어울리는 상의 추천해줘") is True


def test_query_naming_the_same_category_is_still_detected() -> None:
    # 같은 카테고리를 요구해도 질의가 정한 값을 쓴다. VLM은 영어("top"),
    # 질의 키워드는 한국어("상의")라 두 값을 직접 비교할 수 없다.
    assert query_names_a_category("이거랑 비슷한 바지 추천해줘") is True


def test_query_without_a_category_keeps_the_photo_derived_value() -> None:
    # 판단 근거가 없으면 사진에서 읽은 값을 그대로 둔다.
    assert query_names_a_category("이거랑 어울리는 거 추천해줘") is False
