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
    assert item["final_score"] == 0.9


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

    assert "category = :category" in session.statements[0]
    assert session.params[0]["category"] == "바지"


def test_unisex_request_does_not_narrow_by_gender() -> None:
    session = RecordingSession([row("musinsa_1")])

    search(session, query="바지", filters={"gender": "unisex"})

    assert "gender" not in session.params[0]


def test_price_bounds_allow_items_without_a_price() -> None:
    # 가격이 비어 있다고 후보에서 빼면 결과가 지나치게 줄어든다.
    session = RecordingSession([row("musinsa_1")])

    search(session, query="바지", filters={"price_max": 50000})

    assert "price IS NULL OR price <= :price_max" in session.statements[0]
    assert session.params[0]["price_max"] == 50000


def test_candidate_pool_is_wider_than_the_requested_limit() -> None:
    # 메타데이터 가중치를 얹으면 벡터 순위와 최종 순위가 달라진다.
    session = RecordingSession([row("musinsa_1")])

    search(session, query="바지", limit=5)

    assert session.params[0]["candidate_limit"] > 5


def test_target_category_is_inferred_from_the_query() -> None:
    # 벡터 검색만으로는 "바지에 어울리는 상의"에서 상의를 찾지 못한다.
    # 질의 텍스트가 바지 설명으로 가득해 비슷한 바지가 먼저 걸린다.
    session = RecordingSession([row("musinsa_1")])

    search(session, query="이 바지에 어울리는 상의 추천해줘")

    assert session.params[0]["category"] == "상의"


def test_explicit_category_filter_wins_over_inference() -> None:
    session = RecordingSession([row("musinsa_1")])

    search(session, query="이 바지에 어울리는 상의 추천해줘", filters={"category": "가방"})

    assert session.params[0]["category"] == "가방"


def test_no_category_condition_when_the_query_names_none() -> None:
    session = RecordingSession([row("musinsa_1")])

    search(session, query="데일리로 입기 좋은 거 추천해줘")

    assert "category" not in session.params[0]
