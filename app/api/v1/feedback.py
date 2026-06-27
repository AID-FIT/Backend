from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.recommendation import FeedbackEventCreate, FeedbackEventResponse
from app.services.feedback_service import FeedbackService

router = APIRouter()


@router.post("/events", response_model=FeedbackEventResponse)
async def create_feedback_event(
    payload: FeedbackEventCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackEventResponse:
    event = await FeedbackService().record(db, current_user, payload)
    return FeedbackEventResponse(id=event.id, event_type=event.event_type)
