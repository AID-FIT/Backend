import asyncio

from app.services.pgvector_rag_service import PgVectorRagService


class FakeEmbeddingService:
    def __init__(self, dimensions: int = 4) -> None:
        self.dimensions = dimensions
        self.queries: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1] * self.dimensions


class FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self):
        return self._rows


class RecordingSession:
    """실행된 SQL과 파라미터만 붙잡는 최소 더블."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.statements: list[str] = []
        self.params: list[dict] = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        return FakeResult(self.rows)

    @property
    def search_sql(self) -> str:
        """검색 쿼리. 앞에 SET LOCAL 같은 세션 설정이 붙을 수 있어 골라 낸다."""
        return next(statement for statement in self.statements if "SELECT" in statement)

    @property
    def search_params(self) -> dict:
        return self.params[self.statements.index(self.search_sql)]


def row(item_id: str, similarity: float = 0.9, **overrides) -> dict:
    base = {
        "item_id": item_id,
        "source": "musinsa",
        "name": "세미 와이드 데님 팬츠",
        "brand": "Example Brand",
        "category": "바지",
        "label": "슬랙스",
        "gender": "men",
        "color": "black",
        "material": "cotton",
        "fit": "wide",
        "pattern": "solid",
        "mood": "minimal",
        "season": "spring",
        "price": 59000,
        "image_url": "https://image.example/item.jpg",
        "product_url": f"https://www.musinsa.com/products/{item_id}",
        "similarity": similarity,
    }
    base.update(overrides)
    return base


def search(session: RecordingSession, **kwargs):
    service = PgVectorRagService(embedding_service=FakeEmbeddingService())
    return asyncio.run(service.search(session, **kwargs))


def test_empty_query_does_not_touch_the_database() -> None:
    session = RecordingSession()

    items = search(session, query="   ")

    assert items == []
    assert session.statements == []


def test_results_are_mapped_to_the_agent_item_contract() -> None:
    session = RecordingSession([row("musinsa_1")])

    items = search(session, query="와이드 슬랙스", limit=5)

    assert len(items) == 1
    item = items[0]
    assert item["item_id"] == "musinsa_1"
    assert item["item_name"] == "세미 와이드 데님 팬츠"
    assert item["sense_of_season"] == "spring"
    # 최종 점수는 유사도만이 아니다. 카테고리·종류·핏이 맞으면 메타데이터
    # 점수가 붙어 유사도(0.9)보다 낮아지거나 높아진다.
    assert item["similarity_score"] == 0.9
    assert item["final_score"] == round(0.9 * 0.75 + item["metadata_score"] * 0.25, 4)


def test_missing_image_falls_back_to_a_placeholder() -> None:
    # image_url이 비면 클라이언트에서 깨진 이미지가 뜬다.
    session = RecordingSession([row("musinsa_1", image_url=None)])

    items = search(session, query="바지")

    assert items[0]["image_url"].endswith("no_image_500.png")


def test_excluded_refs_are_dropped() -> None:
    session = RecordingSession([row("shown"), row("fresh")])

    items = search(session, query="바지", limit=5, excluded_item_refs={"shown"})

    assert [item["item_id"] for item in items] == ["fresh"]


def test_limit_caps_the_returned_items() -> None:
    session = RecordingSession([row(f"item_{index}") for index in range(10)])

    items = search(session, query="바지", limit=3)

    assert len(items) == 3


def test_category_filter_narrows_the_query() -> None:
    session = RecordingSession([row("musinsa_1")])

    search(session, query="바지", filters={"category": "바지"})

    assert "category = :category" in session.search_sql
    assert session.search_params["category"] == "바지"


def test_unisex_request_does_not_narrow_by_gender() -> None:
    session = RecordingSession([row("musinsa_1")])

    search(session, query="바지", filters={"gender": "unisex"})

    assert "gender" not in session.search_params


def test_price_bounds_allow_items_without_a_price() -> None:
    # 가격이 비어 있다고 후보에서 빼면 결과가 지나치게 줄어든다.
    session = RecordingSession([row("musinsa_1")])

    search(session, query="바지", filters={"price_max": 50000})

    assert "price IS NULL OR price <= :price_max" in session.search_sql
    assert session.search_params["price_max"] == 50000


def test_candidate_pool_is_wider_than_the_requested_limit() -> None:
    # 메타데이터 가중치를 얹으면 벡터 순위와 최종 순위가 달라진다.
    session = RecordingSession([row("musinsa_1")])

    search(session, query="바지", limit=5)

    assert session.search_params["candidate_limit"] > 5


def test_target_category_is_inferred_from_the_query() -> None:
    # 벡터 검색만으로는 "바지에 어울리는 상의"에서 상의를 찾지 못한다.
    # 질의 텍스트가 바지 설명으로 가득해 비슷한 바지가 먼저 걸린다.
    session = RecordingSession([row("musinsa_1")])

    search(session, query="이 바지에 어울리는 상의 추천해줘")

    assert session.search_params["category"] == "상의"


def test_explicit_category_filter_wins_over_inference() -> None:
    session = RecordingSession([row("musinsa_1")])

    search(session, query="이 바지에 어울리는 상의 추천해줘", filters={"category": "가방"})

    assert session.search_params["category"] == "가방"


def test_no_category_condition_when_the_query_names_none() -> None:
    session = RecordingSession([row("musinsa_1")])

    search(session, query="데일리로 입기 좋은 거 추천해줘")

    assert "category" not in session.search_params


def scores(session, **kwargs) -> dict:
    return search(session, **kwargs)[0]


def test_metadata_lifts_a_matching_product_over_a_closer_one() -> None:
    """유사도만으로 자르면 "검정"을 요청해도 흰 옷이 위에 온다.

    문서 전체가 비슷하면 색 한 낱말의 차이는 벡터 거리에 거의 안 남는다.
    맞아야 하는 필드에 명시적으로 점수를 줘야 순위가 뒤집힌다.
    """
    session = RecordingSession([
        row("ivory", similarity=0.92, color="ivory", mood="minimal", fit="regular"),
        row("black", similarity=0.90, color="black", mood="street", fit="wide"),
    ])

    items = search(session, query="검정 스트릿 와이드", limit=2)

    assert [item["item_id"] for item in items] == ["black", "ivory"]


def test_metadata_score_is_reported_not_guessed() -> None:
    # nodes.py의 랭커가 final_score·metadata_score를 읽는다. None이면 유사도만
    # 남아 메타데이터 가중치가 통째로 사라진다.
    session = RecordingSession([row("musinsa_1", color="black")])

    item = scores(session, query="검정 바지")

    assert item["metadata_score"] > 0


def test_the_same_product_page_appears_once() -> None:
    """카탈로그에 한 상품이 여러 행으로 들어 있다.

    고유 product_url 10,500개에 전체 12,794행이다. 그대로 두면 같은 상품이
    피드에 두 번 오른다.
    """
    duplicate_url = "https://www.musinsa.com/products/same"
    session = RecordingSession([
        row("row_a", similarity=0.80, product_url=duplicate_url),
        row("row_b", similarity=0.95, product_url=duplicate_url),
        row("other", similarity=0.70),
    ])

    items = search(session, query="바지", limit=5)

    assert len(items) == 2
    # 중복 중에서는 점수가 높은 쪽을 남긴다.
    assert "row_b" in [item["item_id"] for item in items]


def test_products_without_a_page_are_not_merged_together() -> None:
    # product_url이 비었다고 서로 같은 상품인 것은 아니다.
    session = RecordingSession([
        row("a", product_url=None),
        row("b", product_url=None),
    ])

    items = search(session, query="바지", limit=5)

    assert len(items) == 2


def test_the_embedded_text_expands_korean_into_catalog_words() -> None:
    """카탈로그는 "black"으로 적혀 있고 사용자는 "검정"이라고 친다.

    질의를 그대로 임베딩하면 이 둘이 만나지 못한다.
    """
    embedding = FakeEmbeddingService()
    service = PgVectorRagService(embedding_service=embedding)
    asyncio.run(service.search(RecordingSession([row("musinsa_1")]), query="검정 반팔"))

    assert "black" in embedding.queries[0]
    assert "반팔티" in embedding.queries[0]


def test_the_embedded_text_carries_the_closet_and_taste() -> None:
    # 옷장과 취향은 필터로 만들 수 없는 신호다. 질의에 실어야 임베딩이 잡는다.
    embedding = FakeEmbeddingService()
    service = PgVectorRagService(embedding_service=embedding)
    asyncio.run(service.search(
        RecordingSession([row("musinsa_1")]),
        query="오늘 뭐 입지",
        closet_items=[{"color": "charcoal", "mood": "minimal"}],
        preferred_styles=["스트릿"],
    ))

    assert "charcoal" in embedding.queries[0]
    assert "스트릿" in embedding.queries[0]


def test_the_closet_stays_out_when_the_user_turned_it_off() -> None:
    embedding = FakeEmbeddingService()
    service = PgVectorRagService(embedding_service=embedding)
    asyncio.run(service.search(
        RecordingSession([row("musinsa_1")]),
        query="오늘 뭐 입지",
        closet_items=[{"color": "charcoal"}],
        use_closet_style=False,
    ))

    assert "charcoal" not in embedding.queries[0]


def test_the_category_filter_still_reads_the_users_own_words() -> None:
    """선필터는 확장 전 질의로 정한다.

    별칭을 편 문장에는 "상의"와 "바지"가 함께 들어 있어, 그걸로 추론하면
    "이 바지에 어울리는 상의"가 바지로 좁혀진다.
    """
    session = RecordingSession([row("musinsa_1")])

    search(session, query="이 바지에 어울리는 상의 추천해줘")

    assert session.search_params["category"] == "상의"


def test_hnsw_searches_at_least_as_wide_as_the_limit() -> None:
    """pgvector의 hnsw.ef_search 기본값은 40이다.

    그보다 큰 LIMIT을 걸어도 인덱스는 40건 언저리만 돌려준다. 운영 DB에서
    실측했을 때 LIMIT 400 질의가 40건만 반환했고, 그 40건이 아우터·바지로
    쏠려 가방과 원피스는 0건이었다. 넓히면 같은 질의가 7종을 모두 담는다.
    """
    import re

    from app.services.pgvector_rag_service import candidate_limit_for

    session = RecordingSession([row("musinsa_1")])
    search(session, query="바지", limit=100)

    widening = [s for s in session.statements if "ef_search" in s]
    assert widening, "설정하지 않으면 기본값 40으로 돌아간다"
    assert int(re.search(r"ef_search = (\d+)", widening[0]).group(1)) >= candidate_limit_for(100)


def test_the_widening_does_not_leak_to_other_requests() -> None:
    # 세션 단위로 걸면 pgbouncer가 커넥션을 돌려쓸 때 다른 요청까지 바뀐다.
    session = RecordingSession([row("musinsa_1")])
    search(session, query="바지")

    assert all("SET LOCAL" in s for s in session.statements if "ef_search" in s)
