from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FeedbackEvent, Recommendation, User
from app.schemas.recommendation import FeedbackEventCreate


class FeedbackService:
    async def record(
        self,
        db: AsyncSession,
        user: User,
        payload: FeedbackEventCreate,
    ) -> FeedbackEvent:
        metadata = dict(payload.metadata)

        # recommendation_id가 실제 저장된 추천일 때만 FK로 연결하고, 아니면 metadata에 원본을 남긴다.
        recommendation_id: str | None = None
        if payload.recommendation_id:
            existing = await db.scalar(
                select(Recommendation.id).where(Recommendation.id == payload.recommendation_id)
            )
            if existing is not None:
                recommendation_id = existing
            else:
                metadata.setdefault("recommendation_ref", payload.recommendation_id)

        # 외부(무신사 등) 상품 id는 products 테이블 FK와 충돌하므로 metadata로 보관한다.
        if payload.product_id:
            metadata.setdefault("product_ref", payload.product_id)

        event = FeedbackEvent(
            user_id=user.id,
            recommendation_id=recommendation_id,
            product_id=None,
            event_type=payload.event_type,
            metadata_json=metadata,
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)
        return event
