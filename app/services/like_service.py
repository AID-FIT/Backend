from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProductLike
from app.schemas.like import (
    DEFAULT_LIKE_PAGE_SIZE,
    MAX_LIKE_PAGE_SIZE,
    MAX_LIKED_REFS,
    ProductLikeCreate,
)


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
