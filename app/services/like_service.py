from collections.abc import Iterable
from typing import Any

from sqlalchemy import bindparam, delete, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClosetItem, ProductLike
from app.schemas.like import (
    DEFAULT_LIKE_PAGE_SIZE,
    MAX_LIKE_PAGE_SIZE,
    MAX_LIKED_REFS,
    ProductLikeCreate,
)

PRODUCT_VECTOR_TABLE = "product_vectors"
LIKE_STYLE_FIELDS = ("color", "material", "fit", "pattern", "mood", "sense_of_season")


class ProductNotIdentifiableError(Exception):
    """무엇을 좋아요 했는지 가리킬 값이 하나도 없다."""


def product_ref_of(payload: ProductLikeCreate) -> str:
    """상품을 가리키는 안정적인 키 하나를 고른다.

    카탈로그 item_id가 가장 안정적이고, 없으면 상품 페이지, 그것도 없으면
    이미지 주소로 떨어진다. 채팅 후보를 식별하는 순서와 같게 맞춘다.
    """
    for candidate in (payload.item_id, payload.product_url, payload.image_url):
        text = str(candidate or "").strip()
        if text:
            return text
    raise ProductNotIdentifiableError


class LikeService:
    @staticmethod
    def to_agent_payload(like: ProductLike) -> dict:
        """Convert the saved snapshot into the ranker's internal input."""
        return {
            "product_ref": like.product_ref,
            "source": like.source,
            "name": like.name,
            "image_url": like.image_url,
            "product_url": like.product_url,
        }

    async def list_style_payloads(
        self,
        db: AsyncSession,
        user_id: str,
        limit: int = DEFAULT_LIKE_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """Return saved likes enriched with style metadata already in our DBs.

        Musinsa likes are matched against ``product_vectors`` and closet likes
        against the authenticated user's ``closet_items``. Missing catalog rows
        deliberately fall back to the saved product name.
        """
        likes = await self.list_for_user(db, user_id, limit=limit)
        if not likes:
            return []

        catalog_styles = await self._load_catalog_styles(db, likes)
        closet_styles = await self._load_closet_styles(db, user_id, likes)

        payloads: list[dict[str, Any]] = []
        for like in likes:
            payload = self.to_agent_payload(like)
            lookup = catalog_styles if like.source == "musinsa" else closet_styles
            style = self._matched_style(like, lookup)
            if style:
                payload["name"] = style.get("name") or payload.get("name")
                for field in LIKE_STYLE_FIELDS:
                    if style.get(field) is not None:
                        payload[field] = style[field]
            payloads.append(payload)
        return payloads

    async def _load_catalog_styles(
        self,
        db: AsyncSession,
        likes: list[ProductLike],
    ) -> dict[str, dict[str, Any]]:
        identities = self._identity_values(
            like for like in likes if like.source == "musinsa"
        )
        if not identities:
            return {}

        identity_list = sorted(identities)
        statement = text(
            f"""
            SELECT item_id, name, color, material, fit, pattern, mood, season,
                   image_url, product_url
            FROM {PRODUCT_VECTOR_TABLE}
            WHERE item_id IN :item_ids
               OR product_url IN :product_urls
               OR image_url IN :image_urls
            """
        ).bindparams(
            bindparam("item_ids", expanding=True),
            bindparam("product_urls", expanding=True),
            bindparam("image_urls", expanding=True),
        )
        result = await db.execute(
            statement,
            {
                "item_ids": identity_list,
                "product_urls": identity_list,
                "image_urls": identity_list,
            },
        )

        styles: dict[str, dict[str, Any]] = {}
        for raw_row in result.mappings().all():
            row = dict(raw_row)
            style = {
                "name": row.get("name"),
                "color": row.get("color"),
                "material": row.get("material"),
                "fit": row.get("fit"),
                "pattern": row.get("pattern"),
                "mood": row.get("mood"),
                "sense_of_season": row.get("season"),
            }
            self._index_style(styles, style, row)
        return styles

    async def _load_closet_styles(
        self,
        db: AsyncSession,
        user_id: str,
        likes: list[ProductLike],
    ) -> dict[str, dict[str, Any]]:
        identities = self._identity_values(
            like for like in likes if like.source == "closet"
        )
        if not identities:
            return {}

        identity_list = sorted(identities)
        result = await db.execute(
            select(ClosetItem).where(
                ClosetItem.user_id == user_id,
                or_(
                    ClosetItem.id.in_(identity_list),
                    ClosetItem.product_url.in_(identity_list),
                    ClosetItem.image_url.in_(identity_list),
                ),
            )
        )
        styles: dict[str, dict[str, Any]] = {}
        for item in result.scalars().all():
            style = {
                "name": item.name,
                "color": item.color,
                "material": item.material,
                "fit": item.fit,
                "pattern": item.pattern,
                "mood": item.mood,
                "sense_of_season": item.sense_of_season,
            }
            self._index_style(
                styles,
                style,
                {
                    "item_id": item.id,
                    "product_url": item.product_url,
                    "image_url": item.image_url,
                },
            )
        return styles

    @staticmethod
    def _identity_values(likes: Iterable[ProductLike]) -> set[str]:
        return {
            value
            for like in likes
            for candidate in (like.product_ref, like.product_url, like.image_url)
            if (value := str(candidate or "").strip())
        }

    @staticmethod
    def _index_style(
        target: dict[str, dict[str, Any]],
        style: dict[str, Any],
        identity_source: dict[str, Any],
    ) -> None:
        for key in ("item_id", "product_url", "image_url"):
            identity = str(identity_source.get(key) or "").strip()
            if identity:
                target.setdefault(identity, style)

    @staticmethod
    def _matched_style(
        like: ProductLike,
        styles: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        for identity in (like.product_ref, like.product_url, like.image_url):
            value = str(identity or "").strip()
            if value and value in styles:
                return styles[value]
        return None

    async def like(
        self,
        db: AsyncSession,
        user_id: str,
        payload: ProductLikeCreate,
    ) -> ProductLike:
        """좋아요를 켠다. 이미 켜져 있으면 그대로 둔다.

        다시 눌러도 결과가 같아야 한다. 네트워크가 끊겨 재시도되는 요청이
        오류가 되거나 중복 행을 만들면, 화면의 하트가 실제와 어긋난다.
        """
        product_ref = product_ref_of(payload)
        existing = await self._find(db, user_id, product_ref)
        if existing is not None:
            return existing

        like = ProductLike(
            user_id=user_id,
            product_ref=product_ref,
            source=payload.source,
            name=payload.item_name,
            brand=payload.brand,
            category=payload.category,
            price=payload.price,
            image_url=payload.image_url,
            product_url=payload.product_url,
        )
        db.add(like)
        try:
            await db.commit()
        except IntegrityError:
            # 같은 사용자가 두 번 눌러 요청이 겹쳤다. 유니크 제약이 잡아 준
            # 것이므로 먼저 들어간 행을 돌려준다.
            await db.rollback()
            saved = await self._find(db, user_id, product_ref)
            if saved is None:
                raise
            return saved

        await db.refresh(like)
        return like

    async def unlike(self, db: AsyncSession, user_id: str, product_ref: str) -> bool:
        """좋아요를 끈다. 이미 꺼져 있어도 성공으로 본다."""
        result = await db.execute(
            delete(ProductLike).where(
                ProductLike.user_id == user_id,
                ProductLike.product_ref == product_ref,
            )
        )
        await db.commit()
        return bool(result.rowcount)

    async def list_for_user(
        self,
        db: AsyncSession,
        user_id: str,
        limit: int = DEFAULT_LIKE_PAGE_SIZE,
    ) -> list[ProductLike]:
        page_size = max(1, min(limit, MAX_LIKE_PAGE_SIZE))
        result = await db.execute(
            select(ProductLike)
            .where(ProductLike.user_id == user_id)
            # 같은 시각에 들어간 행의 순서가 흔들리지 않도록 id를 함께 본다.
            .order_by(ProductLike.created_at.desc(), ProductLike.id.desc())
            .limit(page_size)
        )
        return list(result.scalars().all())

    async def list_refs(self, db: AsyncSession, user_id: str) -> list[str]:
        result = await db.execute(
            select(ProductLike.product_ref)
            .where(ProductLike.user_id == user_id)
            .order_by(ProductLike.created_at.desc())
            .limit(MAX_LIKED_REFS)
        )
        return list(result.scalars().all())

    async def _find(
        self, db: AsyncSession, user_id: str, product_ref: str
    ) -> ProductLike | None:
        result = await db.execute(
            select(ProductLike).where(
                ProductLike.user_id == user_id,
                ProductLike.product_ref == product_ref,
            )
        )
        return result.scalar_one_or_none()
