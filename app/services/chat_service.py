from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatConversation, ChatMessage
from app.services.recommendation_service import RecommendationService

# Agent 입력에 다시 넣을 직전 대화 수. 늘릴수록 맥락은 좋아지지만 토큰이 커진다.
DEFAULT_HISTORY_LIMIT = 20
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
# 제목을 따로 주지 않으면 첫 질문 앞부분을 잘라 쓴다.
TITLE_FROM_QUERY_LENGTH = 40


class ChatNotFoundError(Exception):
    """대화가 없거나 요청한 사용자의 것이 아니다."""


class ChatService:
    def __init__(self, recommendation_service: RecommendationService | None = None) -> None:
        self.recommendation_service = recommendation_service or RecommendationService()

    async def create_conversation(
        self, db: AsyncSession, user_id: str, title: str | None = None
    ) -> ChatConversation:
        conversation = ChatConversation(user_id=user_id, title=title)
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)
        return conversation

    async def list_conversations(self, db: AsyncSession, user_id: str) -> list[ChatConversation]:
        result = await db.execute(
            select(ChatConversation)
            .where(ChatConversation.user_id == user_id)
            .order_by(ChatConversation.updated_at.desc(), ChatConversation.id.desc())
        )
        return list(result.scalars().all())

    async def get_owned_conversation(
        self, db: AsyncSession, conversation_id: str, user_id: str
    ) -> ChatConversation | None:
        result = await db.execute(
            select(ChatConversation).where(
                ChatConversation.id == conversation_id,
                ChatConversation.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_messages(
        self,
        db: AsyncSession,
        conversation_id: str,
        user_id: str,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> tuple[list[ChatMessage], str | None]:
        """오래된 순으로 한 페이지를 돌려준다. cursor는 직전 페이지의 마지막 메시지 id."""
        conversation = await self.get_owned_conversation(db, conversation_id, user_id)
        if conversation is None:
            raise ChatNotFoundError

        page_size = max(1, min(limit, MAX_PAGE_SIZE))
        query = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)

        if cursor:
            # 커서 메시지의 (created_at, id) 뒤부터 이어 읽는다.
            anchor = await db.execute(
                select(ChatMessage.created_at, ChatMessage.id).where(ChatMessage.id == cursor)
            )
            anchor_row = anchor.first()
            if anchor_row is not None:
                created_at, message_id = anchor_row
                query = query.where(
                    (ChatMessage.created_at, ChatMessage.id) > (created_at, message_id)
                )

        # 정렬 키에 id를 함께 둬야 같은 시각의 메시지 순서가 흔들리지 않는다.
        query = query.order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc()).limit(page_size + 1)
        rows = list((await db.execute(query)).scalars().all())

        has_more = len(rows) > page_size
        messages = rows[:page_size]
        next_cursor = messages[-1].id if has_more and messages else None
        return messages, next_cursor

    async def send_message(
        self,
        db: AsyncSession,
        user_id: str,
        conversation_id: str,
        query: str,
        image_urls: list[str] | None = None,
    ) -> dict:
        conversation = await self.get_owned_conversation(db, conversation_id, user_id)
        if conversation is None:
            raise ChatNotFoundError

        normalized_image_urls = image_urls or []
        history = await self._load_recent_messages(db, conversation_id, DEFAULT_HISTORY_LIMIT)

        user_message = ChatMessage(
            conversation_id=conversation_id,
            role="user",
            content=query,
            payload={"image_urls": normalized_image_urls},
        )
        db.add(user_message)
        if conversation.title is None:
            conversation.title = query[:TITLE_FROM_QUERY_LENGTH]
        # Agent 호출 전에 커밋한다. 외부 LLM 응답이 오래 걸리는 동안
        # DB 트랜잭션과 락을 잡고 있지 않기 위해서다.
        await db.commit()
        await db.refresh(user_message)

        agent_response = await self.recommendation_service.create(
            query=query,
            user_id=user_id,
            image_urls=normalized_image_urls,
            chat_history=self._serialize_history(history),
        )

        assistant_message = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=agent_response.get("message") or "",
            payload=agent_response,
        )
        db.add(assistant_message)
        await db.commit()
        await db.refresh(assistant_message)

        return {
            "conversation_id": conversation_id,
            "user_message_id": user_message.id,
            "assistant_message_id": assistant_message.id,
            "response": agent_response,
        }

    async def _load_recent_messages(
        self, db: AsyncSession, conversation_id: str, limit: int
    ) -> list[ChatMessage]:
        # 최신 N개를 고른 뒤 시간순으로 되돌려 대화 순서를 유지한다.
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    def _serialize_history(self, messages: list[ChatMessage]) -> list[dict]:
        # payload는 모델에 넣지 않는다. 상품 목록 전체를 다시 보내면 토큰만 커진다.
        return [{"role": message.role, "content": message.content} for message in messages]
