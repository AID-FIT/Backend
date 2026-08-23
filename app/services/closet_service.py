import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClosetItem, ImageAsset, User
from app.services.vlm_service import VlmService

logger = logging.getLogger(__name__)

# 한 요청에서 처리할 최대 장수. VLM 한 건에 수 초가 걸리므로
# 함수 실행 시간(maxDuration) 안에 끝나도록 묶어둔다.
DEFAULT_PENDING_BATCH = 3


class ClosetService:
    def __init__(self, vlm_service: VlmService | None = None) -> None:
        self.vlm_service = vlm_service or VlmService()

    async def reuse_analysis(
        self,
        db: AsyncSession,
        user: User,
        image: ImageAsset,
    ) -> ClosetItem | None:
        return await self.reuse_analysis_for(db, user.id, image)

    async def reuse_analysis_for(
        self,
        db: AsyncSession,
        user_id: str,
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

        return await self._upsert(db, user_id, image, dict(source.raw_vlm_result or {}))

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
        return await self.analyze_and_store_for(db, user.id, image)

    async def analyze_and_store_for(
        self,
        db: AsyncSession,
        user_id: str,
        image: ImageAsset,
    ) -> ClosetItem:
        vlm_result = await self.vlm_service.analyze(image.storage_url)
        return await self._upsert(db, user_id, image, vlm_result)

    async def _upsert(
        self,
        db: AsyncSession,
        user_id: str,
        image: ImageAsset,
        vlm_result: dict,
    ) -> ClosetItem:
        existing = await db.execute(select(ClosetItem).where(ClosetItem.image_id == image.id))
        closet_item = existing.scalar_one_or_none()

        values = {
            "user_id": user_id,
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

    async def list_pending_images(
        self,
        db: AsyncSession,
        user_id: str | None = None,
        limit: int = DEFAULT_PENDING_BATCH,
    ) -> list[ImageAsset]:
        """분석이 남아 있는 이미지를 오래된 것부터 돌려준다.

        업로드와 분석이 분리돼 있어, 분석 요청이 도달하지 못하면(네트워크 끊김,
        앱 종료) 이미지가 메타데이터 없이 남는다. 그 잔여분을 찾는다.
        user_id를 주지 않으면 전체를 훑는다(Cron 용도).
        """
        query = (
            select(ImageAsset)
            .outerjoin(ClosetItem, ClosetItem.image_id == ImageAsset.id)
            .where(ClosetItem.id.is_(None), ImageAsset.user_id.is_not(None))
            .order_by(ImageAsset.created_at.asc())
            .limit(limit)
        )
        if user_id is not None:
            query = query.where(ImageAsset.user_id == user_id)

        result = await db.execute(query)
        return list(result.scalars().all())

    async def analyze_pending(
        self,
        db: AsyncSession,
        user_id: str | None = None,
        limit: int = DEFAULT_PENDING_BATCH,
    ) -> dict:
        """남아 있는 분석을 한 배치만큼 처리한다.

        한 장이 실패해도 나머지는 이어서 처리한다. 실패한 장은 다음 호출에서
        다시 잡히므로, 이 함수 자체가 재시도 장치가 된다.
        """
        pending = await self.list_pending_images(db, user_id=user_id, limit=limit)
        analyzed = 0
        failed = 0

        for image in pending:
            try:
                if await self.reuse_analysis_for(db, image.user_id, image) is None:
                    await self.analyze_and_store_for(db, image.user_id, image)
                await db.commit()
                analyzed += 1
            except Exception:
                # 한 장의 실패가 배치 전체를 되돌리지 않도록 여기서 끊는다.
                await db.rollback()
                failed += 1
                logger.warning("pending analysis failed for image %s", image.id, exc_info=True)

        remaining = len(await self.list_pending_images(db, user_id=user_id, limit=limit))
        return {"analyzed": analyzed, "failed": failed, "has_more": remaining > 0}

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
