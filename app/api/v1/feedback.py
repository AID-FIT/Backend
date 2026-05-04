from uuid import uuid4

from fastapi import APIRouter

from app.schemas.recommendation import FeedbackEventCreate, FeedbackEventResponse

router = APIRouter()


@router.post("/events", response_model=FeedbackEventResponse)
async def create_feedback_event(payload: FeedbackEventCreate) -> FeedbackEventResponse:
    return FeedbackEventResponse(id=f"evt_{uuid4().hex[:12]}", event_type=payload.event_type)

