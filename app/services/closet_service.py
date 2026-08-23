from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClosetItem, ImageAsset, User
from app.services.vlm_service import VlmService


class ClosetService:
    def __init__(self, vlm_service: VlmService | None = None) -> None:
        self.vlm_service = vlm_service or VlmService()

    async def analyze_and_store(
        self,
        db: AsyncSession,
        user: User,
        image: ImageAsset,
    ) -> ClosetItem:
        existing = await db.execute(select(ClosetItem).where(ClosetItem.image_id == image.id))
        closet_item = existing.scalar_one_or_none()
        vlm_result = await self.vlm_service.analyze(image.storage_url)

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
        return await self.list_for_user_id(db, user.id)

    async def list_for_user_id(self, db: AsyncSession, user_id: str) -> list[ClosetItem]:
        result = await db.execute(
            select(ClosetItem)
            .where(ClosetItem.user_id == user_id)
            .order_by(ClosetItem.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    def to_agent_payload(item: ClosetItem) -> dict:
        """Convert a persisted closet row into an agent recommendation candidate."""
        return {
            "closet_item_id": item.id,
            "name": item.name,
            "brand": item.brand,
            "price": item.price,
            "category": item.category,
            "label": item.sub_category,
            "gender": item.gender,
            "image_url": item.image_url,
            "product_url": item.product_url,
            "color": item.color,
            "material": item.material,
            "fit": item.fit,
            "pattern": item.pattern,
            "mood": item.mood,
            "sense_of_season": item.sense_of_season,
        }
