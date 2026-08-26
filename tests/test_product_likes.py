import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import Delete
from sqlalchemy.exc import IntegrityError

from app.db.models import ProductLike
from app.schemas.like import MAX_LIKE_PAGE_SIZE, ProductLikeCreate
from app.services.like_service import (
    LikeService,
    ProductNotIdentifiableError,
    product_ref_of,
)


class FakeResult:
    def __init__(self, values: list) -> None:
        self._values = values

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list:
        return list(self._values)

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None


class FakeMappingResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "FakeMappingResult":
        return self

    def all(self) -> list[dict]:
        return list(self._rows)


class CatalogLookupDb:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[object, dict]] = []

    async def execute(self, statement: object, params: dict) -> FakeMappingResult:
        self.calls.append((statement, params))
        return FakeMappingResult(self.rows)


class StubStyleLikeService(LikeService):
    def __init__(
        self,
        catalog_styles: dict[str, dict] | None = None,
        closet_styles: dict[str, dict] | None = None,
    ) -> None:
        self.catalog_styles = catalog_styles or {}
        self.closet_styles = closet_styles or {}

    async def _load_catalog_styles(self, db, likes):
        return self.catalog_styles

    async def _load_closet_styles(self, db, user_id, likes):
        return self.closet_styles


class FakeLikeDb:
    """product_likes 한 테이블만 흉내 낸다.

    저장소에 테스트 DB가 없어(conftest도 TestClient도 쓰지 않는다) 문장을 보고
    행을 걸러 준다. 확인하려는 것은 소유자 범위, 중복 처리, 정렬이다.
    """

    def __init__(self, rows: list[ProductLike] | None = None) -> None:
        self.rows = list(rows or [])
        self.pending: list[ProductLike] = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_next_commit = False

    def add(self, row: ProductLike) -> None:
        self.pending.append(row)

    async def commit(self) -> None:
        if self.fail_next_commit:
            self.fail_next_commit = False
            self.pending.clear()
            raise IntegrityError("duplicate", None, Exception("uq_product_likes_user_product"))

        for row in self.pending:
            row.id = row.id or f"like-{len(self.rows) + 1}"
            row.created_at = row.created_at or datetime.now(UTC)
            self.rows.append(row)
        self.pending.clear()
        self.commits += 1

    async def rollback(self) -> None:
        self.pending.clear()
        self.rollbacks += 1

    async def refresh(self, row: object) -> None:
        return None

    async def execute(self, statement: object) -> object:
        params = statement.compile().params
        user_id = params.get("user_id_1")
        product_ref = params.get("product_ref_1")

        if isinstance(statement, Delete):
            keep = [
                row
                for row in self.rows
                if not (row.user_id == user_id and row.product_ref == product_ref)
            ]
            removed = len(self.rows) - len(keep)
            self.rows = keep
            return SimpleNamespace(rowcount=removed)

        matched = [row for row in self.rows if row.user_id == user_id]
        if product_ref is not None:
            matched = [row for row in matched if row.product_ref == product_ref]
        else:
            matched.sort(key=lambda row: (row.created_at, row.id), reverse=True)

        limit = getattr(statement._limit_clause, "value", None)
        if limit is not None:
            matched = matched[:limit]

        if statement.column_descriptions[0]["name"] == "product_ref":
            return FakeResult([row.product_ref for row in matched])
        return FakeResult(matched)


def like_row(user_id: str, product_ref: str, minutes_ago: int = 0, **overrides) -> ProductLike:
    return ProductLike(
        id=f"like-{product_ref}",
        user_id=user_id,
        product_ref=product_ref,
        source="musinsa",
        created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        **overrides,
    )


def payload(**overrides) -> ProductLikeCreate:
    return ProductLikeCreate(
        **{
            "item_id": "musinsa_1",
            "source": "musinsa",
            "item_name": "와이드 슬랙스",
            "brand": "Example Brand",
            "category": "바지",
            "price": 59000,
            "image_url": "https://image.example/slacks.jpg",
            "product_url": "https://www.musinsa.com/products/1",
            **overrides,
        }
    )


def test_catalog_id_identifies_the_product() -> None:
    assert product_ref_of(payload()) == "musinsa_1"


def test_product_page_stands_in_when_there_is_no_catalog_id() -> None:
    assert product_ref_of(payload(item_id=None)) == "https://www.musinsa.com/products/1"


def test_image_url_is_the_last_resort() -> None:
    assert product_ref_of(payload(item_id=None, product_url=None)) == "https://image.example/slacks.jpg"


def test_a_product_with_nothing_to_point_at_is_rejected() -> None:
    with pytest.raises(ProductNotIdentifiableError):
        product_ref_of(payload(item_id=None, product_url=None, image_url=None))


def test_liking_stores_a_snapshot_of_the_product() -> None:
    # 목록 화면이 카탈로그를 다시 검색하지 않아도 그릴 수 있어야 한다.
    db = FakeLikeDb()

    like = asyncio.run(LikeService().like(db, "user_001", payload()))

    assert (like.brand, like.name, like.price) == ("Example Brand", "와이드 슬랙스", 59000)
    assert like.image_url == "https://image.example/slacks.jpg"
    assert len(db.rows) == 1


def test_like_snapshot_can_be_sent_to_the_personalization_ranker() -> None:
    saved = like_row(
        "user_001",
        "musinsa_1",
        name="와이드 슬랙스",
        brand="Example Brand",
        category="바지",
        price=59000,
        image_url="https://image.example/slacks.jpg",
        product_url="https://www.musinsa.com/products/1",
    )

    assert LikeService.to_agent_payload(saved) == {
        "product_ref": "musinsa_1",
        "source": "musinsa",
        "name": "와이드 슬랙스",
        "image_url": "https://image.example/slacks.jpg",
        "product_url": "https://www.musinsa.com/products/1",
    }


def test_catalog_style_lookup_batches_all_liked_product_identities() -> None:
    likes = [
        like_row(
            "user_001",
            "musinsa_1",
            image_url="https://image.example/1.jpg",
            product_url="https://www.musinsa.com/products/1",
        ),
        like_row(
            "user_001",
            "https://www.musinsa.com/products/2",
            image_url="https://image.example/2.jpg",
            product_url="https://www.musinsa.com/products/2",
        ),
    ]
    db = CatalogLookupDb(
        [
            {
                "item_id": "musinsa_1",
                "name": "그래픽 셔츠",
                "color": "blue",
                "material": "cotton",
                "fit": "oversized",
                "pattern": "graphic",
                "mood": "street",
                "season": "summer",
                "image_url": "https://image.example/1.jpg",
                "product_url": "https://www.musinsa.com/products/1",
            }
        ]
    )

    styles = asyncio.run(LikeService()._load_catalog_styles(db, likes))

    assert len(db.calls) == 1
    assert set(db.calls[0][1]["item_ids"]) == {
        "musinsa_1",
        "https://www.musinsa.com/products/1",
        "https://image.example/1.jpg",
        "https://www.musinsa.com/products/2",
        "https://image.example/2.jpg",
    }
    assert db.calls[0][1]["product_urls"] == db.calls[0][1]["item_ids"]
    assert db.calls[0][1]["image_urls"] == db.calls[0][1]["item_ids"]
    assert styles["musinsa_1"]["sense_of_season"] == "summer"
    assert styles["https://www.musinsa.com/products/1"]["fit"] == "oversized"
    assert styles["https://image.example/1.jpg"]["mood"] == "street"


def test_saved_likes_are_enriched_with_style_only() -> None:
    musinsa_like = like_row(
        "user_001",
        "musinsa_1",
        name="저장 당시 이름",
        brand="Not Used",
        category="상의",
        price=99000,
    )
    closet_like = ProductLike(
        id="like-closet_1",
        user_id="user_001",
        product_ref="closet_1",
        source="closet",
        name="내 옷",
        brand="Not Used Either",
        category="아우터",
        price=120000,
        created_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    service = StubStyleLikeService(
        catalog_styles={
            "musinsa_1": {
                "name": "카탈로그 상품명",
                "color": "blue",
                "material": "cotton",
                "fit": "oversized",
                "pattern": "graphic",
                "mood": "street",
                "sense_of_season": "summer",
            }
        },
        closet_styles={
            "closet_1": {
                "name": "내 옷장 상품명",
                "color": "black",
                "material": "wool",
                "fit": "regular",
                "pattern": "solid",
                "mood": "minimal",
                "sense_of_season": "winter",
            }
        },
    )
    db = FakeLikeDb([musinsa_like, closet_like])

    payloads = asyncio.run(service.list_style_payloads(db, "user_001"))

    by_ref = {item["product_ref"]: item for item in payloads}
    assert by_ref["musinsa_1"] == {
        "product_ref": "musinsa_1",
        "source": "musinsa",
        "name": "카탈로그 상품명",
        "image_url": None,
        "product_url": None,
        "color": "blue",
        "material": "cotton",
        "fit": "oversized",
        "pattern": "graphic",
        "mood": "street",
        "sense_of_season": "summer",
    }
    assert by_ref["closet_1"]["mood"] == "minimal"
    assert by_ref["closet_1"]["sense_of_season"] == "winter"
    assert "brand" not in by_ref["musinsa_1"]
    assert "category" not in by_ref["musinsa_1"]
    assert "price" not in by_ref["musinsa_1"]


def test_missing_catalog_style_falls_back_to_saved_product_name() -> None:
    saved = like_row(
        "user_001",
        "removed_product",
        name="블루 오버핏 스트릿 셔츠",
    )
    db = FakeLikeDb([saved])

    payloads = asyncio.run(StubStyleLikeService().list_style_payloads(db, "user_001"))

    assert payloads == [
        {
            "product_ref": "removed_product",
            "source": "musinsa",
            "name": "블루 오버핏 스트릿 셔츠",
            "image_url": None,
            "product_url": None,
        }
    ]


def test_liking_the_same_product_twice_keeps_one_row() -> None:
    # 다시 눌러도 결과가 같아야 한다. 재시도된 요청이 중복을 만들면 안 된다.
    db = FakeLikeDb()
    service = LikeService()

    first = asyncio.run(service.like(db, "user_001", payload()))
    second = asyncio.run(service.like(db, "user_001", payload()))

    assert len(db.rows) == 1
    assert first.id == second.id


def test_a_racing_duplicate_returns_the_row_that_won() -> None:
    # 두 요청이 겹치면 유니크 제약이 잡는다. 그때 오류를 내보내지 않는다.
    winner = like_row("user_001", "musinsa_1")
    db = FakeLikeDb()
    db.fail_next_commit = True
    # 커밋이 실패한 뒤 다시 조회할 때는 먼저 들어간 행이 보인다.
    original_commit = db.commit

    async def commit_then_reveal() -> None:
        try:
            await original_commit()
        except IntegrityError:
            db.rows.append(winner)
            raise

    db.commit = commit_then_reveal

    like = asyncio.run(LikeService().like(db, "user_001", payload()))

    assert like is winner
    assert db.rollbacks == 1


def test_unliking_removes_it() -> None:
    db = FakeLikeDb([like_row("user_001", "musinsa_1")])

    removed = asyncio.run(LikeService().unlike(db, "user_001", "musinsa_1"))

    assert removed is True
    assert db.rows == []


def test_unliking_something_that_is_not_liked_is_still_a_success() -> None:
    # 두 번 눌렀다고 오류를 보여줄 이유가 없다.
    db = FakeLikeDb()

    assert asyncio.run(LikeService().unlike(db, "user_001", "musinsa_1")) is False


def test_one_user_cannot_unlike_anothers() -> None:
    db = FakeLikeDb([like_row("user_002", "musinsa_1")])

    removed = asyncio.run(LikeService().unlike(db, "user_001", "musinsa_1"))

    assert removed is False
    assert len(db.rows) == 1


def test_the_list_is_newest_first() -> None:
    db = FakeLikeDb(
        [
            like_row("user_001", "old", minutes_ago=30),
            like_row("user_001", "new", minutes_ago=1),
            like_row("user_001", "middle", minutes_ago=10),
        ]
    )

    likes = asyncio.run(LikeService().list_for_user(db, "user_001"))

    assert [like.product_ref for like in likes] == ["new", "middle", "old"]


def test_the_list_only_holds_your_own() -> None:
    db = FakeLikeDb([like_row("user_001", "mine"), like_row("user_002", "theirs")])

    likes = asyncio.run(LikeService().list_for_user(db, "user_001"))

    assert [like.product_ref for like in likes] == ["mine"]


def test_an_oversized_page_is_capped() -> None:
    db = FakeLikeDb([like_row("user_001", f"item-{index}") for index in range(5)])

    likes = asyncio.run(LikeService().list_for_user(db, "user_001", limit=10_000))

    # 상한을 넘겨 요청해도 서버가 정한 만큼만 돌려준다.
    assert len(likes) <= MAX_LIKE_PAGE_SIZE


def test_refs_carry_identifiers_only() -> None:
    # 하트를 채울지만 판단하면 되므로 상품 정보를 실어 보내지 않는다.
    db = FakeLikeDb([like_row("user_001", "musinsa_1"), like_row("user_002", "musinsa_2")])

    refs = asyncio.run(LikeService().list_refs(db, "user_001"))

    assert refs == ["musinsa_1"]


def test_like_request_rejects_an_unknown_source() -> None:
    with pytest.raises(ValidationError):
        payload(source="coupang")


def test_like_request_rejects_fields_the_server_decides() -> None:
    # user_id와 product_ref는 서버가 정한다.
    with pytest.raises(ValidationError):
        ProductLikeCreate(source="musinsa", item_id="musinsa_1", user_id="someone-else")

    with pytest.raises(ValidationError):
        ProductLikeCreate(source="musinsa", item_id="musinsa_1", product_ref="forged")
