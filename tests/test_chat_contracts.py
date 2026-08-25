import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.db.models import ChatMessage
from app.schemas.chat import ChatMessageResponse, MessageSendRequest
from app.services.chat_service import ChatService


class FakeChatDb:
    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    def add(self, value: object) -> None:
        if isinstance(value, ChatMessage):
            value.id = f"message-{len(self.messages) + 1}"
            self.messages.append(value)

    async def commit(self) -> None:
        return None

    async def refresh(self, value: object) -> None:
        return None


class FakeChatClosetService:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.user_ids: list[str] = []

    async def list_for_user_id(self, db: object, user_id: str) -> list[dict]:
        self.user_ids.append(user_id)
        return self.items

    @staticmethod
    def to_agent_payload(item: dict) -> dict:
        return item


class FakeChatUserService:
    def __init__(self, preference: object | None) -> None:
        self.preference = preference
        self.user_ids: list[str] = []

    async def get_preference_for_user_id(self, db: object, user_id: str) -> object | None:
        self.user_ids.append(user_id)
        return self.preference


class CapturingRecommendationService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {
            "response": {
                "status": "empty",
                "message": "조건에 맞는 추천 상품을 찾지 못했습니다.",
                "recommendations": [],
                "style_guide": None,
            },
            "candidate_pool": [],
            "retrieval_action": "retrieve",
            "retrieval_target": "closet",
        }


class StubChatService(ChatService):
    async def get_owned_conversation(
        self,
        db: object,
        conversation_id: str,
        user_id: str,
    ) -> object:
        return SimpleNamespace(title=None)

    async def _load_recent_messages(
        self,
        db: object,
        conversation_id: str,
        limit: int,
    ) -> list[ChatMessage]:
        return []


def test_message_send_request_accepts_query_and_images() -> None:
    request = MessageSendRequest(
        query="조금 더 저렴한 제품으로 추천해줘",
        image_urls=["https://cdn.aidfit.com/item_001.jpg"],
    )

    assert request.query == "조금 더 저렴한 제품으로 추천해줘"
    assert request.image_urls == ["https://cdn.aidfit.com/item_001.jpg"]


def test_message_send_request_rejects_client_supplied_role() -> None:
    # role은 서버가 정한다. 클라이언트가 assistant를 사칭할 수 없어야 한다.
    with pytest.raises(ValidationError):
        MessageSendRequest(query="추천해줘", role="assistant")


def test_message_send_request_rejects_client_supplied_user_id() -> None:
    # 소유자는 액세스 토큰에서만 온다.
    with pytest.raises(ValidationError):
        MessageSendRequest(query="추천해줘", user_id="someone-else")


def test_message_send_request_rejects_empty_query() -> None:
    with pytest.raises(ValidationError):
        MessageSendRequest(query="")


def test_serialized_history_carries_only_role_and_content() -> None:
    # payload에는 추천 상품 목록 전체가 들어 있어 모델 입력에 넣으면 토큰만 커진다.
    messages = [
        ChatMessage(
            conversation_id="conversation-1",
            role="user",
            content="검은색 재킷에 어울리는 바지 추천해줘",
            payload={"image_urls": ["https://cdn.aidfit.com/jacket.jpg"]},
        ),
        ChatMessage(
            conversation_id="conversation-1",
            role="assistant",
            content="회색 와이드 슬랙스를 추천합니다.",
            payload={"status": "success", "recommendations": [{"item_id": "musinsa_1"}]},
        ),
    ]

    history = ChatService()._serialize_history(messages)

    assert history == [
        {"role": "user", "content": "검은색 재킷에 어울리는 바지 추천해줘"},
        {"role": "assistant", "content": "회색 와이드 슬랙스를 추천합니다."},
    ]


def test_chat_sends_authenticated_users_closet_and_profile_to_agent() -> None:
    closet_payload = {
        "closet_item_id": "closet_001",
        "name": "내 와이드 팬츠",
        "image_url": "https://cdn.aidfit.com/closet_001.jpg",
        "category": "하의",
    }
    closet_service = FakeChatClosetService([closet_payload])
    user_service = FakeChatUserService(
        SimpleNamespace(styles=["minimal", "casual"], sizes={"age_range": "20s"})
    )
    recommendation_service = CapturingRecommendationService()
    service = StubChatService(
        recommendation_service=recommendation_service,
        closet_service=closet_service,
        user_service=user_service,
    )

    asyncio.run(
        service.send_message(
            db=FakeChatDb(),
            user_id="user_001",
            conversation_id="conversation_001",
            query="이 옷에 어울리는 옷을 옷장에서 찾아줘",
            image_urls=["https://cdn.aidfit.com/reference.jpg"],
        )
    )

    assert closet_service.user_ids == ["user_001"]
    assert user_service.user_ids == ["user_001"]
    assert recommendation_service.calls[0]["closet_items"] == [closet_payload]
    assert recommendation_service.calls[0]["user_profile"] == {
        "age_group": "20s",
        "preferred_styles": ["minimal", "casual"],
    }


def test_chat_service_restores_private_previous_rag_context() -> None:
    rag_item = {
        "item_id": "musinsa_1",
        "source": "musinsa",
        "name": "와이드 슬랙스",
        "image_url": "https://image.example/slacks.jpg",
        "product_url": "https://www.musinsa.com/products/musinsa_1",
    }
    messages = [
        ChatMessage(
            conversation_id="conversation-1",
            role="assistant",
            content="회색 와이드 슬랙스를 추천합니다.",
            payload={
                "status": "success",
                "message": "추천 결과",
                "_agent_context": {
                    "rag_items": [rag_item],
                    "rag_query": "검은 재킷과 어울리는 바지",
                    "retrieval_target": "musinsa",
                },
            },
        )
    ]

    context = ChatService()._extract_previous_agent_context(messages)

    assert context["rag_items"] == [rag_item]
    assert context["rag_query"] == "검은 재킷과 어울리는 바지"
    assert context["retrieval_target"] == "musinsa"


def test_private_agent_context_is_not_serialized_to_chat_api() -> None:
    message = ChatMessageResponse(
        id="message-1",
        conversation_id="conversation-1",
        role="assistant",
        content="추천 결과입니다.",
        payload={
            "status": "success",
            "message": "추천 결과입니다.",
            "_agent_context": {"rag_items": [{"item_id": "internal"}]},
        },
        created_at=datetime.now(UTC),
    )

    payload = message.model_dump()["payload"]

    assert payload["status"] == "success"
    assert "_agent_context" not in payload


def test_reused_turn_preserves_candidate_pool_and_accumulates_shown_refs() -> None:
    candidate_pool = [
        {"item_id": "shown", "source": "musinsa"},
        {"item_id": "next", "source": "musinsa"},
        {"item_id": "later", "source": "musinsa"},
    ]
    retrieved_at = datetime.now(UTC).isoformat()
    previous_context = {
        "candidate_pool": candidate_pool,
        "shown_item_refs": ["shown"],
        "rag_query": "검은 재킷에 어울리는 팬츠",
        "retrieval_target": "musinsa",
        "retrieved_at": retrieved_at,
    }
    trace = {
        "response": {
            "recommendations": [{"item_id": "next"}],
        },
        "candidate_pool": candidate_pool,
        "rag_items": [candidate_pool[1]],
        "shown_item_refs": ["shown", "next"],
        "retrieval_target": "musinsa",
        "resolved_query": "비슷한 팬츠 하나 더",
        "rag_reused": True,
    }

    context = ChatService()._build_agent_context(trace, previous_context)

    assert context["candidate_pool"] == candidate_pool
    assert context["shown_item_refs"] == ["shown", "next"]
    assert context["rag_query"] == "검은 재킷에 어울리는 팬츠"
    assert context["retrieved_at"] == retrieved_at


def test_expired_private_candidate_context_is_not_reused() -> None:
    expired_at = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    message = ChatMessage(
        conversation_id="conversation-1",
        role="assistant",
        content="추천 결과입니다.",
        payload={
            "_agent_context": {
                "candidate_pool": [{"item_id": "expired"}],
                "shown_item_refs": [],
                "retrieved_at": expired_at,
            }
        },
    )

    context = ChatService(candidate_cache_ttl_seconds=60)._extract_previous_agent_context([message])

    assert context["candidate_pool"] == []
    assert context["shown_item_refs"] == []
