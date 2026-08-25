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
    is_vague_search_request,
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


def test_vague_search_text_uses_taste_only_when_explicitly_enabled() -> None:
    text = build_search_text(
        "오늘 뭐 입지",
        preferred_styles=["스트릿"],
        use_preference_search=True,
    )

    assert "스트릿" in text


def test_specific_search_text_does_not_mix_in_taste_by_default() -> None:
    text = build_search_text(
        "흰색 셔츠 추천해줘",
        preferred_styles=["검정", "스트릿"],
    )

    assert "black" not in text
    assert "스트릿" not in text


def test_only_context_free_requests_are_vague() -> None:
    assert is_vague_search_request("오늘 뭐 입지") is True
    assert is_vague_search_request("나한테 어울리는 거 추천해줘") is True
    assert is_vague_search_request("흰색 셔츠 추천해줘") is False
    assert is_vague_search_request("비 오는 날 입을 옷 추천해줘") is False


def test_reference_or_explicit_filter_disables_preference_search() -> None:
    assert is_vague_search_request("추천해줘", has_reference_items=True) is False
    assert is_vague_search_request("추천해줘", filters={"category": "상의"}) is False


def test_coordination_search_text_does_not_append_raw_reference_attributes() -> None:
    text = build_search_text(
        "검은 데님 바지와 어울리는 상의",
        vlm_items=[{"category": "바지", "color": "black", "material": "corduroy"}],
        request_mode="coordination",
    )

    assert "검은 데님 바지와 어울리는 상의" in text
    assert "corduroy" not in text


def test_search_text_stays_short_enough_to_embed() -> None:
    # 질의가 길어지면 뒤쪽 토큰이 임베딩에서 묻힌다.
    text = build_search_text(
        "검정",
        vlm_items=[{"color": "black " * 200}],
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


def test_metadata_score_contains_no_personalization_for_a_generic_query() -> None:
    assert metadata_score(item(), infer_query_intents("추천해줘")) == 0.0


def test_similarity_still_carries_most_of_the_weight() -> None:
    # 메타데이터가 유사도를 이기면 요청과 무관한 상품이 조건만 맞다고 올라온다.
    assert SIMILARITY_WEIGHT > METADATA_WEIGHT
    assert final_score(1.0, 0.0) > final_score(0.0, 1.0)


def test_the_final_score_blends_both() -> None:
    assert final_score(0.8, 0.4) == pytest.approx(0.8 * 0.75 + 0.4 * 0.25)
