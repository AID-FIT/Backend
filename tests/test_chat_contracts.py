import pytest
from pydantic import ValidationError

from app.db.models import ChatMessage
from app.schemas.chat import MessageSendRequest
from app.services.chat_service import ChatService


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
