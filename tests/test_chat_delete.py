import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import Delete

from app.services.chat_service import ChatNotFoundError, ChatService


class FakeScalarResult:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def scalars(self) -> "FakeScalarResult":
        return self

    def all(self) -> list[str]:
        return list(self._values)


class FakeDeleteDb:
    """삭제가 어떤 문장을 어떤 순서로 내는지만 본다.

    저장소에 테스트 DB가 없어(conftest도 TestClient도 쓰지 않는다) 문장을 직접
    들여다본다. 확인하려는 것은 두 가지다 — 메시지를 먼저 지우는지, 그리고
    모든 WHERE에 소유자가 걸려 있는지.
    """

    def __init__(
        self,
        owned_conversation_ids: list[str] | None = None,
        conversation_rowcount: int = 1,
    ) -> None:
        self.owned_conversation_ids = owned_conversation_ids or []
        self.conversation_rowcount = conversation_rowcount
        self.deleted_tables: list[str] = []
        self.params: list[dict] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: object) -> object:
        if isinstance(statement, Delete):
            self.deleted_tables.append(statement.table.name)
            self.params.append(statement.compile().params)
            rowcount = (
                self.conversation_rowcount
                if statement.table.name == "chat_conversations"
                else len(self.owned_conversation_ids)
            )
            return SimpleNamespace(rowcount=rowcount)
        return FakeScalarResult(self.owned_conversation_ids)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class OwnedChatService(ChatService):
    async def get_owned_conversation(self, db: object, conversation_id: str, user_id: str) -> object:
        return SimpleNamespace(id=conversation_id, user_id=user_id)


class UnownedChatService(ChatService):
    async def get_owned_conversation(self, db: object, conversation_id: str, user_id: str) -> None:
        return None


def test_deleting_a_conversation_removes_its_messages_first() -> None:
    # 대화를 먼저 지우면 그 사이 실패했을 때 주인 없는 메시지가 남는다.
    db = FakeDeleteDb()

    asyncio.run(OwnedChatService().delete_conversation(db, "conversation_001", "user_001"))

    assert db.deleted_tables == ["chat_messages", "chat_conversations"]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_conversation_delete_is_scoped_to_its_owner() -> None:
    db = FakeDeleteDb()

    asyncio.run(OwnedChatService().delete_conversation(db, "conversation_001", "user_001"))

    conversation_params = db.params[-1]
    assert "conversation_001" in conversation_params.values()
    assert "user_001" in conversation_params.values()


def test_another_users_conversation_is_not_found_rather_than_forbidden() -> None:
    # 404와 403이 갈리면 남의 대화가 존재한다는 사실이 새어 나간다.
    db = FakeDeleteDb()

    with pytest.raises(ChatNotFoundError):
        asyncio.run(UnownedChatService().delete_conversation(db, "conversation_001", "user_001"))

    assert db.deleted_tables == []
    assert db.commits == 0


def test_a_conversation_deleted_in_between_rolls_the_message_delete_back() -> None:
    # 소유권 확인과 삭제 사이에 다른 요청이 먼저 지운 경우.
    db = FakeDeleteDb(conversation_rowcount=0)

    with pytest.raises(ChatNotFoundError):
        asyncio.run(OwnedChatService().delete_conversation(db, "conversation_001", "user_001"))

    assert db.rollbacks == 1
    assert db.commits == 0


def test_deleting_every_conversation_reports_how_many_went() -> None:
    db = FakeDeleteDb(owned_conversation_ids=["conversation_001", "conversation_002"], conversation_rowcount=2)

    deleted = asyncio.run(ChatService().delete_all_conversations(db, "user_001"))

    assert deleted == 2
    assert db.deleted_tables == ["chat_messages", "chat_conversations"]
    assert db.commits == 1


def test_deleting_every_conversation_only_touches_that_user() -> None:
    db = FakeDeleteDb(owned_conversation_ids=["conversation_001"], conversation_rowcount=1)

    asyncio.run(ChatService().delete_all_conversations(db, "user_001"))

    message_params, conversation_params = db.params
    # 메시지는 그 사용자가 가진 대화 id로만 좁혀진다(IN 파라미터는 목록으로 묶인다).
    assert list(message_params.values()) == [["conversation_001"]]
    assert "user_001" in conversation_params.values()


def test_deleting_nothing_is_still_a_success() -> None:
    # 빈 상태에서 다시 눌러도 같은 결과여야 한다.
    db = FakeDeleteDb(owned_conversation_ids=[])

    deleted = asyncio.run(ChatService().delete_all_conversations(db, "user_001"))

    assert deleted == 0
    assert db.deleted_tables == []
    assert db.commits == 0
