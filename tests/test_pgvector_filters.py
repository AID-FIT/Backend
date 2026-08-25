"""pgvector 선필터와 새로고침 회전.

카탈로그의 mood·color·season은 "casual, street"처럼 쉼표로 이어진 다중값이다.
`=` 비교로는 하나도 걸리지 않아, 오랫동안 이 필터들이 조용히 무시되고 있었다.
"""

import re
import sqlite3

from app.services.pgvector_rag_service import PgVectorRagService

# 실제 카탈로그에서 뽑은 값의 모양. mood·color·season이 모두 쉼표 다중값이다.
CATALOG = [
    ("street-tee", "casual, street", "black, white", "summer"),
    ("minimal-knit", "casual, minimal", "ivory", "spring, fall"),
    ("sporty-cap", "sporty, casual", "black", "all"),
    ("classic-coat", "casual, classic", "charcoal", "fall, winter"),
]


def conditions(**filters) -> tuple[str, dict]:
    condition_list, params = PgVectorRagService()._build_conditions(filters)
    return " AND ".join(condition_list), params


def selected(**filters) -> set[str]:
    """만들어진 SQL 조건을 실제로 실행해 걸리는 행을 본다.

    조건 문자열을 파이썬으로 흉내 내면 SQL이 틀려도 테스트가 통과한다.
    sqlite는 `||`·`lower()`·`LIKE`를 Postgres와 같은 의미로 처리하므로,
    문자열 연산만 검증하는 이 목적에는 충분하다.
    """
    sql, params = conditions(**filters)
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE product_vectors (item_id TEXT, mood TEXT, color TEXT, season TEXT)")
    connection.executemany("INSERT INTO product_vectors VALUES (?, ?, ?, ?)", CATALOG)

    where = f"WHERE {sql}" if sql else ""
    statement = f"SELECT item_id FROM product_vectors {where}"
    # SQLAlchemy의 :name 자리표시자를 sqlite의 :name 그대로 쓸 수 있다.
    rows = connection.execute(statement, params).fetchall()
    connection.close()
    return {row[0] for row in rows}


def test_multi_value_mood_matches_every_listed_value() -> None:
    assert selected(mood="street") == {"street-tee"}


def test_multi_value_mood_matches_the_first_value_too() -> None:
    assert selected(mood="casual") == {"street-tee", "minimal-knit", "sporty-cap", "classic-coat"}


def test_word_fragments_do_not_match() -> None:
    # LIKE '%cas%'로 짰다면 네 건 전부 걸렸을 것이다. 낱말 경계가 지켜져야 한다.
    assert selected(mood="cas") == set()


def test_color_matches_inside_a_multi_value_string() -> None:
    assert selected(color="white") == {"street-tee"}
    assert selected(color="black") == {"street-tee", "sporty-cap"}


def test_season_condition_keeps_all_season_items() -> None:
    # 카탈로그의 41%가 "all"이다. 함께 허용하지 않으면 계절을 고르는 순간
    # 사계절 상품이 통째로 사라진다.
    assert selected(season="summer") == {"street-tee", "sporty-cap"}


def test_season_matches_a_later_value_in_the_list() -> None:
    assert selected(season="winter") == {"classic-coat", "sporty-cap"}


def test_all_season_request_does_not_narrow_anything() -> None:
    sql, params = conditions(season="all")

    assert sql == ""
    assert params == {}


def test_sense_of_season_is_accepted_as_season() -> None:
    # VLM은 sense_of_season으로, 홈 엔드포인트는 season으로 부른다.
    _sql, params = conditions(sense_of_season="winter")

    assert params["season"] == "winter"


def test_filters_combine_with_and() -> None:
    assert selected(mood="casual", color="black") == {"street-tee", "sporty-cap"}


def test_style_filters_are_not_turned_into_conditions() -> None:
    # product_vectors에 대응 컬럼이 없다. 조건으로 만들면 항상 0건이 된다.
    sql, params = conditions(style="minimal", preferred_styles=["스트릿"])

    assert sql == ""
    assert params == {}


def test_category_stays_an_exact_match() -> None:
    sql, params = conditions(category="바지")

    assert re.search(r"\bcategory = :category\b", sql)
    assert params["category"] == "바지"


def item(index: int) -> dict:
    return {"item_id": f"item_{index}"}


CANDIDATES = [item(index) for index in range(20)]


def rotate(seed: int, limit: int = 5) -> list[str]:
    rotated = PgVectorRagService()._rotate(CANDIDATES, limit, seed)
    return [candidate["item_id"] for candidate in rotated[:limit]]


def test_refreshing_returns_a_different_set() -> None:
    # 벡터 검색은 결정적이라 seed가 없으면 새로고침해도 화면이 그대로였다.
    assert rotate(0) != rotate(1)
    assert rotate(1) != rotate(2)


def test_first_load_keeps_the_best_matches_on_top() -> None:
    assert rotate(0) == ["item_0", "item_1", "item_2", "item_3", "item_4"]


def test_rotation_drops_nothing() -> None:
    assert len(PgVectorRagService()._rotate(CANDIDATES, 5, 3)) == len(CANDIDATES)


def test_rotation_wraps_around_instead_of_running_out() -> None:
    # seed가 후보 수를 넘어가도 빈 목록이 나오면 안 된다.
    assert len(rotate(99)) == 5


def test_rotation_survives_an_empty_candidate_list() -> None:
    assert PgVectorRagService()._rotate([], 5, 3) == []


def test_a_home_sized_pool_still_rotates_into_fresh_products() -> None:
    """홈처럼 크게 뽑는 요청도 새로고침에서 새 상품이 나와야 한다.

    후보 상한이 뽑는 개수의 배수에 못 미치면 회전한 창이 겹쳐, 새로고침을
    눌러도 이미 본 상품이 다시 올라온다.
    """
    from app.api.v1.recommendations import _HOME_CANDIDATE_POOL
    from app.services.pgvector_rag_service import candidate_limit_for

    pool = _HOME_CANDIDATE_POOL
    candidates = [item(index) for index in range(candidate_limit_for(pool))]
    service = PgVectorRagService()

    def window(seed: int) -> set[str]:
        rotated = service._rotate(candidates, pool, seed)[:pool]
        return {candidate["item_id"] for candidate in rotated}

    assert window(0).isdisjoint(window(1))
    assert window(1).isdisjoint(window(2))
