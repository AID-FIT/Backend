from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ChatConversation, ChatMessage, ClosetItem
from app.services.closet_service import ClosetService
from app.services.recommendation_service import RecommendationService
from app.services.user_service import UserService, to_agent_profile

# Agent 입력에 다시 넣을 직전 대화 수. 늘릴수록 맥락은 좋아지지만 토큰이 커진다.
DEFAULT_HISTORY_LIMIT = 20
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
# 제목을 따로 주지 않으면 첫 질문 앞부분을 잘라 쓴다.
TITLE_FROM_QUERY_LENGTH = 40


class ChatNotFoundError(Exception):
    """대화가 없거나 요청한 사용자의 것이 아니다."""


class ClosetItemNotFoundError(Exception):
    """고른 옷장 아이템이 없거나 요청한 사용자의 것이 아니다."""


# 아무것도 고르지 않은 턴(옷장 전체)을 가리키는 값. 선택 기능이 생기기 전에
# 쌓인 대화도 모두 이 상태였으므로, 키가 없는 옛 컨텍스트는 이것으로 읽는다.
CLOSET_SCOPE_ALL = "all"


def closet_scope_key(requested_ids: list[str]) -> str:
    """이번 턴의 옷장 범위를 한 문자열로 만든다.

    직전 턴의 후보 풀을 재사용할지 판단할 때, 범위가 그대로인지 비교하는 데 쓴다.
    순서가 달라도 같은 범위이므로 정렬해서 만든다.
    """
    return ",".join(sorted(requested_ids)) if requested_ids else CLOSET_SCOPE_ALL


class ChatService:
    def __init__(
        self,
        recommendation_service: RecommendationService | None = None,
        closet_service: ClosetService | None = None,
        candidate_cache_ttl_seconds: int | None = None,
        user_service: UserService | None = None,
    ) -> None:
        self.recommendation_service = recommendation_service or RecommendationService()
        self.closet_service = closet_service or ClosetService()
        self.user_service = user_service or UserService()
        self.candidate_cache_ttl_seconds = (
            settings.rag_candidate_cache_ttl_seconds
            if candidate_cache_ttl_seconds is None
            else candidate_cache_ttl_seconds
        )

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
        closet_item_ids: list[str] | None = None,
    ) -> dict:
        conversation = await self.get_owned_conversation(db, conversation_id, user_id)
        if conversation is None:
            raise ChatNotFoundError

        normalized_image_urls = image_urls or []
        selected_items, requested_ids = await self._resolve_closet_scope(
            db, user_id, closet_item_ids
        )
        scope_key = closet_scope_key(requested_ids)
        history = await self._load_recent_messages(db, conversation_id, DEFAULT_HISTORY_LIMIT)
        previous_context = self._extract_previous_agent_context(history, scope_key)
        closet_payload = [self.closet_service.to_agent_payload(item) for item in selected_items]
        preference = await self.user_service.get_preference_for_user_id(db, user_id)
        user_profile = to_agent_profile(preference)

        user_message = ChatMessage(
            conversation_id=conversation_id,
            role="user",
            content=query,
            payload={
                "image_urls": normalized_image_urls,
                # 고른 경우에만 남긴다. 매 턴 옷장 전체를 메시지에 박으면 히스토리가 부푼다.
                # id만 두면 나중에 그 옷을 지웠을 때 히스토리가 깨진 참조를 그리므로,
                # 다시 그릴 만큼의 얇은 스냅샷을 함께 저장한다.
                "closet_items": [
                    {
                        "closet_item_id": item.id,
                        "name": item.name,
                        "image_url": item.image_url,
                        "category": item.category,
                    }
                    for item in selected_items
                ]
                if requested_ids
                else [],
            },
        )
        db.add(user_message)
        if conversation.title is None:
            conversation.title = query[:TITLE_FROM_QUERY_LENGTH]
        # Agent 호출 전에 커밋한다. 외부 LLM 응답이 오래 걸리는 동안
        # DB 트랜잭션과 락을 잡고 있지 않기 위해서다.
        await db.commit()
        await db.refresh(user_message)

        agent_result = await self.recommendation_service.create(
            query=query,
            user_id=user_id,
            image_urls=normalized_image_urls,
            closet_items=closet_payload,
            user_profile=user_profile,
            chat_history=self._serialize_history(history),
            previous_rag_results=previous_context.get("candidate_pool", []),
            previous_shown_item_refs=previous_context.get("shown_item_refs", []),
            previous_rag_query=previous_context.get("rag_query"),
            previous_retrieval_target=previous_context.get("retrieval_target"),
            return_trace=True,
        )
        trace = agent_result if isinstance(agent_result.get("response"), dict) else {"response": agent_result}
        agent_response = trace["response"]
        assistant_payload = dict(agent_response)
        agent_context = self._build_agent_context(trace, previous_context, scope_key)
        if agent_context["candidate_pool"] and trace.get("retrieval_action") in {"reuse", "retrieve"}:
            # Persist full candidates for the next-turn reuse decision. The public
            # response serializer removes this private key from message payloads.
            assistant_payload["_agent_context"] = agent_context

        assistant_message = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=agent_response.get("message") or "",
            payload=assistant_payload,
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

    async def _resolve_closet_scope(
        self,
        db: AsyncSession,
        user_id: str,
        closet_item_ids: list[str] | None,
    ) -> tuple[list[ClosetItem], list[str]]:
        """이번 턴이 참고할 옷장 아이템과, 사용자가 실제로 고른 id를 돌려준다.

        아무것도 고르지 않았으면 지금까지처럼 옷장 전체를 본다. 골랐다면 그 옷만
        범위가 된다. 소유권은 따로 검사하지 않는다 — 목록 자체가 이미 user_id로
        걸러져 있어서, 남의 아이템 id는 그냥 "없는 id"가 된다. 존재 여부를
        403으로 알려주지 않기 위해서이기도 하다.
        """
        requested_ids = list(dict.fromkeys(closet_item_ids or []))
        closet_items = await self.closet_service.list_for_user_id(db, user_id)
        if not requested_ids:
            return closet_items, []

        items_by_id = {item.id: item for item in closet_items}
        if any(item_id not in items_by_id for item_id in requested_ids):
            raise ClosetItemNotFoundError
        return [items_by_id[item_id] for item_id in requested_ids], requested_ids

    async def delete_conversation(
        self, db: AsyncSession, conversation_id: str, user_id: str
    ) -> None:
        """대화와 그 메시지를 함께 지운다.

        ORM의 `db.delete(conversation)`은 쓰지 않는다. delete-orphan cascade를
        적용하려고 `conversation.messages`를 지연 로딩하는데, AsyncSession에서는
        그 순간 MissingGreenlet으로 터진다. Core delete 두 문장이면 FK에
        ON DELETE CASCADE가 실제로 걸려 있는지와 무관하게 결과가 같다.
        """
        if await self.get_owned_conversation(db, conversation_id, user_id) is None:
            raise ChatNotFoundError

        await db.execute(delete(ChatMessage).where(ChatMessage.conversation_id == conversation_id))
        result = await db.execute(
            delete(ChatConversation).where(
                ChatConversation.id == conversation_id,
                ChatConversation.user_id == user_id,
            )
        )
        if result.rowcount == 0:
            # 소유권 확인과 삭제 사이에 다른 요청이 먼저 지웠다. 메시지 삭제까지
            # 되돌려야 다른 사람의 대화를 건드린 흔적이 남지 않는다.
            await db.rollback()
            raise ChatNotFoundError

        await db.commit()

    async def delete_all_conversations(self, db: AsyncSession, user_id: str) -> int:
        """그 사용자의 대화를 모두 지우고 지운 수를 돌려준다. 없으면 0."""
        conversation_ids = list(
            (
                await db.execute(
                    select(ChatConversation.id).where(ChatConversation.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        if not conversation_ids:
            return 0

        await db.execute(
            delete(ChatMessage).where(ChatMessage.conversation_id.in_(conversation_ids))
        )
        result = await db.execute(
            delete(ChatConversation).where(ChatConversation.user_id == user_id)
        )
        await db.commit()
        return result.rowcount or 0

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

    def _extract_previous_agent_context(
        self, messages: list[ChatMessage], scope_key: str = CLOSET_SCOPE_ALL
    ) -> dict:
        """Find the latest reusable recommendation context in recent messages.

        후보 풀은 그때의 옷장 범위로 만들어진 것이다. 사용자가 이번 턴에 참고할
        옷을 바꿨다면 그 풀은 더 이상 이 질문의 후보가 아니므로, 재사용하지 않고
        빈 컨텍스트를 돌려 재검색시킨다.
        """
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.role != "assistant" or not isinstance(message.payload, dict):
                continue

            context = message.payload.get("_agent_context")
            if isinstance(context, dict):
                candidate_pool = context.get("candidate_pool")
                if not isinstance(candidate_pool, list):
                    candidate_pool = context.get("rag_items")
                if isinstance(candidate_pool, list) and candidate_pool:
                    # 키가 없는 컨텍스트는 선택 기능 이전에 쌓인 것이고, 그때는
                    # 모두 옷장 전체였다.
                    if context.get("closet_scope_key", CLOSET_SCOPE_ALL) != scope_key:
                        return self._empty_agent_context()
                    if not self._context_is_fresh(context, message):
                        return self._empty_agent_context()
                    normalized_pool = [item for item in candidate_pool if isinstance(item, dict)]
                    shown_item_refs = context.get("shown_item_refs")
                    if not isinstance(shown_item_refs, list):
                        shown_item_refs = self._item_refs(message.payload.get("recommendations"))
                    normalized_shown_refs = list(
                        dict.fromkeys(
                            str(item_ref).strip()
                            for item_ref in shown_item_refs
                            if str(item_ref).strip()
                        )
                    )
                    return {
                        "candidate_pool": normalized_pool,
                        "rag_items": normalized_pool,
                        "shown_item_refs": normalized_shown_refs,
                        "rag_query": context.get("rag_query"),
                        "retrieval_target": context.get("retrieval_target"),
                        "retrieved_at": context.get("retrieved_at"),
                    }

            # Backward compatibility for conversations written before private
            # agent context was introduced: recommendation cards are still valid
            # (though smaller) prior candidates.
            recommendations = message.payload.get("recommendations")
            legacy_items = self._recommendations_to_rag_items(recommendations)
            if not legacy_items:
                continue
            # 이 카드들은 선택 기능이 없던 시절에 옷장 전체로 만들어졌다.
            if scope_key != CLOSET_SCOPE_ALL:
                return self._empty_agent_context()
            if not self._context_is_fresh({}, message):
                return self._empty_agent_context()
            previous_query = next(
                (
                    messages[previous_index].content
                    for previous_index in range(index - 1, -1, -1)
                    if messages[previous_index].role == "user"
                ),
                None,
            )
            sources = {item.get("source") for item in legacy_items}
            if sources == {"closet"}:
                target = "closet"
            elif sources == {"musinsa"}:
                target = "musinsa"
            else:
                target = "hybrid"
            return {
                "candidate_pool": legacy_items,
                "rag_items": legacy_items,
                "shown_item_refs": self._item_refs(legacy_items),
                "rag_query": previous_query,
                "retrieval_target": target,
                "retrieved_at": self._message_timestamp(message),
            }
        return self._empty_agent_context()

    def _recommendations_to_rag_items(self, value: object) -> list[dict]:
        if not isinstance(value, list):
            return []
        items: list[dict] = []
        for recommendation in value:
            if not isinstance(recommendation, dict):
                continue
            image_url = recommendation.get("image_url")
            source = recommendation.get("source")
            product_url = recommendation.get("product_url")
            if not image_url or source not in {"closet", "musinsa"}:
                continue
            if source == "musinsa" and not product_url:
                continue
            items.append(
                {
                    "item_id": recommendation.get("item_id"),
                    "source": source,
                    "name": recommendation.get("item_name"),
                    "brand": recommendation.get("brand"),
                    "price": recommendation.get("price"),
                    "category": recommendation.get("category"),
                    "image_url": image_url,
                    "product_url": product_url,
                }
            )
        return items

    def _build_agent_context(
        self,
        trace: dict,
        previous_context: dict | None = None,
        scope_key: str = CLOSET_SCOPE_ALL,
    ) -> dict:
        previous = previous_context or self._empty_agent_context()
        candidate_pool = trace.get("candidate_pool") or trace.get("rag_items") or []
        rag_reused = bool(trace.get("rag_reused"))
        shown_item_refs = trace.get("shown_item_refs")
        if not isinstance(shown_item_refs, list):
            current_refs = self._item_refs((trace.get("response") or {}).get("recommendations"))
            shown_item_refs = [
                *((previous.get("shown_item_refs") or []) if rag_reused else []),
                *current_refs,
            ]
        return {
            "schema_version": 3,
            "closet_scope_key": scope_key,
            "candidate_pool": candidate_pool,
            # Keep the old key while existing conversations and older servers coexist.
            "rag_items": candidate_pool,
            "shown_item_refs": list(dict.fromkeys(shown_item_refs)),
            "rag_query": (
                previous.get("rag_query")
                if rag_reused
                else trace.get("rag_query") or trace.get("resolved_query")
            ),
            "retrieval_target": trace.get("retrieval_target") or previous.get("retrieval_target"),
            "retrieved_at": (
                previous.get("retrieved_at")
                if rag_reused and previous.get("retrieved_at")
                else datetime.now(UTC).isoformat()
            ),
        }

    def _context_is_fresh(self, context: dict, message: ChatMessage) -> bool:
        if self.candidate_cache_ttl_seconds <= 0:
            return True

        raw_timestamp = context.get("retrieved_at") or getattr(message, "created_at", None)
        if raw_timestamp is None:
            # Legacy unsaved test fixtures and old rows without a timestamp remain usable.
            return True
        if isinstance(raw_timestamp, datetime):
            retrieved_at = raw_timestamp
        else:
            try:
                retrieved_at = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
            except ValueError:
                return False
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=UTC)
        age_seconds = (datetime.now(UTC) - retrieved_at.astimezone(UTC)).total_seconds()
        return age_seconds <= self.candidate_cache_ttl_seconds

    def _item_refs(self, items: object) -> list[str]:
        if not isinstance(items, list):
            return []
        refs: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("item_id", "product_url", "image_url"):
                item_ref = str(item.get(key) or "").strip()
                if item_ref:
                    refs.append(item_ref)
                    break
        return list(dict.fromkeys(refs))

    def _message_timestamp(self, message: ChatMessage) -> str | None:
        created_at = getattr(message, "created_at", None)
        if not isinstance(created_at, datetime):
            return None
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return created_at.isoformat()

    def _empty_agent_context(self) -> dict:
        return {
            "candidate_pool": [],
            "rag_items": [],
            "shown_item_refs": [],
            "rag_query": None,
            "retrieval_target": None,
            "retrieved_at": None,
        }
