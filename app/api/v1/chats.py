from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.chat import (
    ChatMessageListResponse,
    ChatMessageResponse,
    ConversationCreateRequest,
    ConversationResponse,
    MessageSendRequest,
    MessageSendResponse,
)
from app.services.chat_service import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, ChatNotFoundError, ChatService

router = APIRouter()


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    payload: ConversationCreateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    conversation = await ChatService().create_conversation(
        db=db,
        user_id=current_user.id,
        title=payload.title if payload else None,
    )
    return ConversationResponse.model_validate(conversation)


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationResponse]:
    conversations = await ChatService().list_conversations(db=db, user_id=current_user.id)
    return [ConversationResponse.model_validate(conversation) for conversation in conversations]


@router.get("/{conversation_id}/messages", response_model=ChatMessageListResponse)
async def list_messages(
    conversation_id: str,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatMessageListResponse:
    try:
        messages, next_cursor = await ChatService().list_messages(
            db=db,
            conversation_id=conversation_id,
            user_id=current_user.id,
            limit=limit,
            cursor=cursor,
        )
    except ChatNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found") from None

    return ChatMessageListResponse(
        messages=[ChatMessageResponse.model_validate(message) for message in messages],
        next_cursor=next_cursor,
    )


@router.post("/{conversation_id}/messages", response_model=MessageSendResponse)
async def send_message(
    conversation_id: str,
    payload: MessageSendRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageSendResponse:
    # 소유자는 액세스 토큰에서만 온다. 클라이언트가 user_id나 role을 지정할 수 없다.
    try:
        result = await ChatService().send_message(
            db=db,
            user_id=current_user.id,
            conversation_id=conversation_id,
            query=payload.query,
            image_urls=payload.image_urls,
        )
    except ChatNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found") from None

    return MessageSendResponse(**result)
