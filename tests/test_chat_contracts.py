import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.db.models import ChatMessage
from app.schemas.chat import MAX_SELECTED_CLOSET_ITEMS, ChatMessageResponse, MessageSendRequest
from app.services.chat_service import (
    CLOSET_SCOPE_ALL,
    ChatService,
    ClosetItemNotFoundError,
    closet_scope_key,
)


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
    def to_agent_payload(item: object) -> dict:
        # 실제 ClosetService는 ORM 행을 dict로 바꾼다. 선택 범위를 다루는
        # 테스트는 id가 필요해 객체를 넣으므로 양쪽을 모두 받는다.
        if isinstance(item, dict):
            return item
        return {"closet_item_id": item.id, "name": item.name, "category": item.category}


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
        SimpleNamespace(
            styles=["minimal", "casual"],
            sizes={"age_range": "20s"},
            gender="men",
            height_cm=178,
        )
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
        "gender": "men",
        "height_cm": 178,
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


def _closet_row(item_id: str, name: str, category: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        name=name,
        category=category,
        image_url=f"https://cdn.aidfit.com/{item_id}.jpg",
    )


def _send_with_closet_selection(
    closet_rows: list[SimpleNamespace],
    closet_item_ids: list[str] | None,
) -> tuple[CapturingRecommendationService, FakeChatDb]:
    db = FakeChatDb()
    recommendation_service = CapturingRecommendationService()
    service = StubChatService(
        recommendation_service=recommendation_service,
        closet_service=FakeChatClosetService(closet_rows),
        user_service=FakeChatUserService(None),
    )

    asyncio.run(
        service.send_message(
            db=db,
            user_id="user_001",
            conversation_id="conversation_001",
            query="이 바지에 어울리는 상의 찾아줘",
            closet_item_ids=closet_item_ids,
        )
    )
    return recommendation_service, db


def test_message_send_request_accepts_selected_closet_items() -> None:
    request = MessageSendRequest(query="추천해줘", closet_item_ids=["closet_001", "closet_002"])

    assert request.closet_item_ids == ["closet_001", "closet_002"]


def test_message_send_request_defaults_to_the_whole_closet() -> None:
    # 아무것도 고르지 않은 요청은 지금까지의 동작(옷장 전체)을 그대로 유지한다.
    assert MessageSendRequest(query="추천해줘").closet_item_ids == []


def test_message_send_request_rejects_more_closet_items_than_the_cap() -> None:
    with pytest.raises(ValidationError):
        MessageSendRequest(
            query="추천해줘",
            closet_item_ids=[f"closet_{index}" for index in range(MAX_SELECTED_CLOSET_ITEMS + 1)],
        )


def test_selecting_closet_items_narrows_what_the_agent_sees() -> None:
    rows = [
        _closet_row("closet_001", "와이드 팬츠", "하의"),
        _closet_row("closet_002", "검은 재킷", "아우터"),
        _closet_row("closet_003", "흰 셔츠", "상의"),
    ]

    recommendation_service, _ = _send_with_closet_selection(rows, ["closet_002"])

    assert recommendation_service.calls[0]["closet_items"] == [
        {"closet_item_id": "closet_002", "name": "검은 재킷", "category": "아우터"}
    ]


def test_unselected_turn_still_sends_the_whole_closet() -> None:
    rows = [
        _closet_row("closet_001", "와이드 팬츠", "하의"),
        _closet_row("closet_002", "검은 재킷", "아우터"),
    ]

    recommendation_service, _ = _send_with_closet_selection(rows, [])

    assert [item["closet_item_id"] for item in recommendation_service.calls[0]["closet_items"]] == [
        "closet_001",
        "closet_002",
    ]


def test_selection_keeps_the_order_the_user_chose_and_drops_duplicates() -> None:
    rows = [
        _closet_row("closet_001", "와이드 팬츠", "하의"),
        _closet_row("closet_002", "검은 재킷", "아우터"),
    ]

    recommendation_service, _ = _send_with_closet_selection(
        rows, ["closet_002", "closet_001", "closet_002"]
    )

    assert [item["closet_item_id"] for item in recommendation_service.calls[0]["closet_items"]] == [
        "closet_002",
        "closet_001",
    ]


def test_closet_item_that_is_not_the_users_own_is_rejected() -> None:
    # 목록이 이미 user_id로 걸러져 있어, 남의 아이템은 "없는 id"와 구별되지 않는다.
    rows = [_closet_row("closet_001", "와이드 팬츠", "하의")]

    with pytest.raises(ClosetItemNotFoundError):
        _send_with_closet_selection(rows, ["closet_999"])


def test_rejected_selection_does_not_write_a_message() -> None:
    # 실패한 요청이 대화에 흔적을 남기면 히스토리가 사실과 어긋난다.
    db = FakeChatDb()
    service = StubChatService(
        recommendation_service=CapturingRecommendationService(),
        closet_service=FakeChatClosetService([_closet_row("closet_001", "팬츠", "하의")]),
        user_service=FakeChatUserService(None),
    )

    with pytest.raises(ClosetItemNotFoundError):
        asyncio.run(
            service.send_message(
                db=db,
                user_id="user_001",
                conversation_id="conversation_001",
                query="추천해줘",
                closet_item_ids=["closet_999"],
            )
        )

    assert db.messages == []


def test_selected_closet_items_are_snapshotted_on_the_user_message() -> None:
    # id만 남기면 나중에 그 옷을 지웠을 때 히스토리가 빈 자리를 그린다.
    rows = [_closet_row("closet_001", "와이드 팬츠", "하의")]

    _, db = _send_with_closet_selection(rows, ["closet_001"])

    assert db.messages[0].payload["closet_items"] == [
        {
            "closet_item_id": "closet_001",
            "name": "와이드 팬츠",
            "image_url": "https://cdn.aidfit.com/closet_001.jpg",
            "category": "하의",
        }
    ]


def test_unselected_turn_does_not_bloat_the_message_with_the_whole_closet() -> None:
    rows = [_closet_row("closet_001", "와이드 팬츠", "하의")]

    _, db = _send_with_closet_selection(rows, [])

    assert db.messages[0].payload["closet_items"] == []


def _assistant_message_with_scope(scope_key: str | None) -> ChatMessage:
    context = {
        "candidate_pool": [{"item_id": "musinsa_1", "source": "musinsa"}],
        "shown_item_refs": ["musinsa_1"],
        "rag_query": "검은 재킷에 어울리는 팬츠",
        "retrieval_target": "musinsa",
        "retrieved_at": datetime.now(UTC).isoformat(),
    }
    if scope_key is not None:
        context["closet_scope_key"] = scope_key
    return ChatMessage(
        conversation_id="conversation-1",
        role="assistant",
        content="추천 결과입니다.",
        payload={"_agent_context": context},
    )


def test_changing_the_closet_scope_forces_a_fresh_search() -> None:
    # 이전 후보 풀은 그때 고른 옷을 기준으로 만들어졌다. 참고할 옷이 바뀌면
    # 그 풀은 더 이상 이 질문의 후보가 아니다.
    message = _assistant_message_with_scope("closet_001")

    context = ChatService()._extract_previous_agent_context([message], "closet_002")

    assert context["candidate_pool"] == []


def test_same_closet_scope_still_reuses_the_candidate_pool() -> None:
    message = _assistant_message_with_scope("closet_001")

    context = ChatService()._extract_previous_agent_context([message], "closet_001")

    assert [item["item_id"] for item in context["candidate_pool"]] == ["musinsa_1"]


def test_context_written_before_selection_existed_counts_as_the_whole_closet() -> None:
    message = _assistant_message_with_scope(None)

    reused = ChatService()._extract_previous_agent_context([message], CLOSET_SCOPE_ALL)
    invalidated = ChatService()._extract_previous_agent_context([message], "closet_001")

    assert [item["item_id"] for item in reused["candidate_pool"]] == ["musinsa_1"]
    assert invalidated["candidate_pool"] == []


def test_legacy_recommendation_cards_are_not_reused_after_a_selection() -> None:
    # 추천 카드만 남은 옛 대화도 옷장 전체로 만들어진 결과다.
    message = ChatMessage(
        conversation_id="conversation-1",
        role="assistant",
        content="추천 결과입니다.",
        payload={
            "recommendations": [
                {
                    "item_id": "musinsa_1",
                    "source": "musinsa",
                    "image_url": "https://image.example/slacks.jpg",
                    "product_url": "https://www.musinsa.com/products/musinsa_1",
                }
            ]
        },
    )

    assert ChatService()._extract_previous_agent_context([message], "closet_001")["candidate_pool"] == []
    assert ChatService()._extract_previous_agent_context([message], CLOSET_SCOPE_ALL)["candidate_pool"]


def test_stored_context_records_the_scope_it_was_built_for() -> None:
    trace = {
        "response": {"recommendations": [{"item_id": "musinsa_1"}]},
        "candidate_pool": [{"item_id": "musinsa_1", "source": "musinsa"}],
        "retrieval_target": "musinsa",
        "resolved_query": "검은 재킷에 어울리는 팬츠",
    }

    context = ChatService()._build_agent_context(trace, None, "closet_001")

    assert context["closet_scope_key"] == "closet_001"
    assert context["schema_version"] == 3


def test_scope_key_ignores_the_order_the_items_were_picked_in() -> None:
    assert closet_scope_key(["b", "a"]) == closet_scope_key(["a", "b"])
    assert closet_scope_key([]) == CLOSET_SCOPE_ALL
