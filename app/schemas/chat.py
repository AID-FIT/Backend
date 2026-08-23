from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.recommendation import AgentResponse


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


class ChatMessageListResponse(BaseModel):
    messages: list[ChatMessageResponse]
    # 다음 페이지 요청에 그대로 넘길 커서. 더 없으면 None.
    next_cursor: str | None = None


class MessageSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # role과 user_id는 서버가 정한다. 클라이언트가 지정하지 못하게 둔다.
    query: str = Field(min_length=1)
    image_urls: list[str] = Field(default_factory=list)


class MessageSendResponse(BaseModel):
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    response: AgentResponse
