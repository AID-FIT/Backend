from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClosetItem, ImageAsset, User
from app.services.vlm_service import VlmService


class ClosetService:
    def __init__(self, vlm_service: VlmService | None = None) -> None:
        self.vlm_service = vlm_service or VlmService()

    async def reuse_analysis(
        self,
        db: AsyncSession,
        user: User,
        image: ImageAsset,
    ) -> ClosetItem | None:
        """같은 내용의 사진이 이미 분석돼 있으면 결과를 복사한다. 없으면 None.

        저장 경로가 내용 해시라 URL이 같으면 픽셀이 같다. 같은 사진을 다시
        분석해도 결과가 달라질 이유가 없어 VLM 호출을 건너뛴다.
        """
        analyzed = await db.execute(
            select(ClosetItem)
            .join(ImageAsset, ClosetItem.image_id == ImageAsset.id)
            .where(
                ImageAsset.storage_url == image.storage_url,
                ClosetItem.image_id != image.id,
            )
            .limit(1)
        )
        source = analyzed.scalar_one_or_none()
        if source is None:
            return None

        return await self._upsert(db, user, image, dict(source.raw_vlm_result or {}))

    async def has_analysis(self, db: AsyncSession, image: ImageAsset) -> bool:
        result = await db.execute(
            select(ClosetItem.id).where(ClosetItem.image_id == image.id).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def analyze_and_store(
        self,
        db: AsyncSession,
        user: User,
        image: ImageAsset,
    ) -> ClosetItem:
        vlm_result = await self.vlm_service.analyze(image.storage_url)
        return await self._upsert(db, user, image, vlm_result)

    async def _upsert(
        self,
        db: AsyncSession,
        user: User,
        image: ImageAsset,
        vlm_result: dict,
    ) -> ClosetItem:
        existing = await db.execute(select(ClosetItem).where(ClosetItem.image_id == image.id))
        closet_item = existing.scalar_one_or_none()

        values = {
            "user_id": user.id,
            "image_id": image.id,
            "name": str(vlm_result.get("name") or "옷장 아이템"),
            "brand": str(vlm_result.get("brand") or "unknown"),
            "price": vlm_result.get("price"),
            "category": str(vlm_result.get("category") or "unknown"),
            "sub_category": vlm_result.get("sub_category"),
            "gender": vlm_result.get("gender"),
            "image_url": str(vlm_result.get("image_url") or image.storage_url),
            "product_url": vlm_result.get("product_url"),
            "color": vlm_result.get("color"),
            "material": vlm_result.get("material"),
            "fit": vlm_result.get("fit"),
            "pattern": vlm_result.get("pattern"),
            "mood": vlm_result.get("mood"),
            "sense_of_season": vlm_result.get("sense_of_season") or vlm_result.get("sense of season"),
            "is_match": bool(vlm_result.get("is_match", True)),
            "raw_vlm_result": vlm_result,
        }

        if closet_item is None:
            closet_item = ClosetItem(**values)
            db.add(closet_item)
        else:
            for key, value in values.items():
                setattr(closet_item, key, value)

        await db.flush()
        return closet_item

    async def list_for_user(self, db: AsyncSession, user: User) -> list[ClosetItem]:
        result = await db.execute(
            select(ClosetItem)
            .where(ClosetItem.user_id == user.id)
            .order_by(ClosetItem.created_at.desc())
        )
        return list(result.scalars().all())
