"""카탈로그 검색의 순수 로직 — 별칭 확장과 메타데이터 점수.

이 계층은 pgvector로 검색을 다시 쓰면서 통째로 빠져 있었다. 빠져도 검색은
성공하고 결과도 나오기 때문에 오랫동안 드러나지 않았다.
"""

import pytest

from app.services.catalog_matching import (
    METADATA_WEIGHT,
    SIMILARITY_WEIGHT,
    build_search_text,
    expand_query,
    final_score,
    infer_query_intents,
    metadata_score,
)


def test_korean_words_reach_the_english_catalog() -> None:
    # 카탈로그는 "black"으로 적혀 있고 사용자는 "검정"이라고 친다.
    expanded = expand_query("검정 스트릿")

    assert "black" in expanded
    assert "street" in expanded


def test_expansion_keeps_the_original_words() -> None:
    assert "검정" in expand_query("검정 바지")


def test_expansion_does_not_repeat_a_word() -> None:
    # 같은 낱말이 여러 번 실리면 임베딩에서 그 축만 부풀어 오른다.
    expanded = expand_query("검정 검은색 블랙").split()

    assert len(expanded) == len(set(expanded))


def test_tokens_land_in_the_field_they_describe() -> None:
    intents = infer_query_intents("검정 오버핏 스트릿 반팔")

    assert intents["color"] == {"black"}
    assert intents["fit"] == {"oversized"}
    assert intents["mood"] == {"street"}
    assert "상의" in intents["category"]


def test_a_query_that_names_nothing_has_no_intents() -> None:
    intents = infer_query_intents("오늘 뭐 입지")

    assert not any(intents.values())


def test_filters_count_as_intents() -> None:
    # 칩으로 고른 조건도 점수에 반영돼야 한다.
    intents = infer_query_intents("추천해줘", {"category": "가방", "season": "winter"})

    assert intents["category"] == {"가방"}
    assert intents["season"] == {"winter"}


def test_search_text_carries_the_closet_and_taste() -> None:
    text = build_search_text(
        "오늘 뭐 입지",
        closet_items=[{"color": "charcoal", "mood": "minimal"}],
        preferred_styles=["스트릿"],
    )

    assert "charcoal" in text
    assert "minimal" in text
    assert "스트릿" in text


def test_search_text_drops_the_closet_when_it_is_turned_off() -> None:
    text = build_search_text(
        "오늘 뭐 입지",
        closet_items=[{"color": "charcoal"}],
        use_closet_style=False,
    )

    assert "charcoal" not in text


def test_search_text_stays_short_enough_to_embed() -> None:
    # 질의가 길어지면 뒤쪽 토큰이 임베딩에서 묻힌다.
    text = build_search_text(
        "검정",
        closet_items=[{"color": "black " * 200}],
    )

    assert len(text.split()) <= 120


def item(**overrides) -> dict:
    base = {
        "category": "상의",
        "label": "니트",
        "color": "black, white",
        "fit": "oversized",
        "pattern": "solid",
        "mood": "casual, street",
        "sense_of_season": "fall, winter",
    }
    base.update(overrides)
    return base


def score(query: str, **overrides) -> float:
    return metadata_score(item(**overrides), infer_query_intents(query))


def test_a_matching_color_scores_higher_than_a_missing_one() -> None:
    assert score("검정") > score("검정", color="ivory")


def test_multi_value_fields_match_on_any_one_value() -> None:
    # 이 컬럼들은 "casual, street"처럼 여러 값을 한 문자열에 담고 있다.
    assert score("스트릿") > 0


def test_a_partial_word_does_not_count_as_a_match() -> None:
    # "red"가 "covered"에 걸리면 안 되듯, 낱말 단위로 봐야 한다.
    assert score("빨강", color="covered") == 0


def test_an_all_season_product_fits_any_season() -> None:
    # 카탈로그의 상당수가 사계절 상품이다. 계절을 고를 때 함께 허용하지
    # 않으면 후보가 통째로 사라진다.
    assert score("겨울", sense_of_season="all") > 0


def test_the_score_never_leaves_zero_to_one() -> None:
    everything = score("검정 오버핏 스트릿 니트 겨울 상의")

    assert 0.0 <= everything <= 1.0


def test_preferred_styles_lift_a_product_in_that_mood() -> None:
    with_taste = metadata_score(
        item(), infer_query_intents("추천해줘"), preferred_styles=["street"]
    )
    without_taste = metadata_score(item(), infer_query_intents("추천해줘"))

    assert with_taste > without_taste


def test_owning_similar_clothes_lifts_a_product() -> None:
    with_closet = metadata_score(
        item(),
        infer_query_intents("추천해줘"),
        closet_items=[{"color": "black", "mood": "street"}],
    )
    without_closet = metadata_score(item(), infer_query_intents("추천해줘"))

    assert with_closet > without_closet


def test_similarity_still_carries_most_of_the_weight() -> None:
    # 메타데이터가 유사도를 이기면 요청과 무관한 상품이 조건만 맞다고 올라온다.
    assert SIMILARITY_WEIGHT > METADATA_WEIGHT
    assert final_score(1.0, 0.0) > final_score(0.0, 1.0)


def test_the_final_score_blends_both() -> None:
    assert final_score(0.8, 0.4) == pytest.approx(0.8 * 0.75 + 0.4 * 0.25)
