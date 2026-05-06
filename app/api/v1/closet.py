from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import ClosetItem, User
from app.db.session import get_db
from app.schemas.closet import ClosetItemResponse
from app.services.closet_service import ClosetService

router = APIRouter()


def _closet_item_response(item: ClosetItem) -> ClosetItemResponse:
    return ClosetItemResponse(
        id=item.id,
        image_id=item.image_id,
        name=item.name,
        brand=item.brand,
        price=item.price,
        category=item.category,
        sub_category=item.sub_category,
        gender=item.gender,
        image_url=item.image_url,
        product_url=item.product_url,
        color=item.color,
        material=item.material,
        fit=item.fit,
        pattern=item.pattern,
        mood=item.mood,
        sense_of_season=item.sense_of_season,
        is_match=item.is_match,
    )


@router.get("/items", response_model=list[ClosetItemResponse])
async def list_closet_items(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ClosetItemResponse]:
    items = await ClosetService().list_for_user(db, current_user)
    return [_closet_item_response(item) for item in items]
