from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.schemas.recommendation import AgentResponse

# 한 번에 참고할 옷장 아이템 수 상한. 사진 첨부 상한과 같은 값으로 둔다.
# 더 늘리면 프롬프트만 커지고, 고른 옷의 신호가 서로를 희석한다.
MAX_SELECTED_CLOSET_ITEMS = 8


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255)


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str | None = None
    created_at: datetime
    updated_at: datetime


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    role: Literal["user", "assistant"]
    content: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_serializer("payload")
    def serialize_public_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Full RAG candidates are persisted for agent continuity, not exposed as
        # part of the public chat-history contract.
        return {key: value for key, value in payload.items() if not key.startswith("_")}


class ChatMessageListResponse(BaseModel):
    messages: list[ChatMessageResponse]
    # 다음 페이지 요청에 그대로 넘길 커서. 더 없으면 None.
    next_cursor: str | None = None


class MessageSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # role과 user_id는 서버가 정한다. 클라이언트가 지정하지 못하게 둔다.
    query: str = Field(min_length=1)
    image_urls: list[str] = Field(default_factory=list)
    # 비우면 지금까지처럼 옷장 전체를 본다. 고르면 그 옷만 이번 턴의 범위가 된다.
    closet_item_ids: list[str] = Field(
        default_factory=list, max_length=MAX_SELECTED_CLOSET_ITEMS
    )


class MessageSendResponse(BaseModel):
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    response: AgentResponse
