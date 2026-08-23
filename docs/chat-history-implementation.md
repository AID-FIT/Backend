# 채팅 내역 저장 및 연속 대화 구현안

> 구현 상태: 아래의 기본 채팅 저장 구조에 더해 LLM 기반 intent 분류, VLM 결합 query rewrite, 검색 대상 결정, 기존 RAG 후보 재사용/재검색 판단까지 구현되었습니다. 후보 풀과 누적 노출 상품 ID, 검색 질의·대상·조회 시각은 assistant 메시지의 `_agent_context`에 내부 저장되며 API 직렬화 시 제거됩니다. 같은 의도의 추가 추천은 미노출 후보만 재사용하고, 후보 소진 또는 TTL 만료 시 새 검색으로 전환합니다. 아래 문서는 최초 설계 배경과 데이터 모델 설명을 보존한 기록입니다.

## 1. 배경

LLM은 요청 간 상태를 자체적으로 유지하지 않는다. 따라서 연속 대화를 지원하려면 다음 두 작업이 모두 필요하다.

1. 사용자의 질문과 Agent의 답변을 DB에 저장한다.
2. 새로운 질문이 들어올 때 이전 대화를 DB에서 조회하여 Agent 입력에 다시 포함한다.

현재 `RecommendationRequest.prompt`와 `Recommendation.raw_agent_output`에 한 번의 추천 요청과 결과는 저장할 수 있지만, 대화 단위 식별자와 메시지 순서가 없어 일반적인 채팅 내역으로 사용하기에는 부족하다.

## 2. 목표

- 사용자별로 여러 개의 대화를 생성할 수 있다.
- 하나의 대화 안에 `user` 질문과 `assistant` 답변을 순서대로 저장한다.
- 과거 대화 내용을 조회할 수 있다.
- 최근 대화를 Agent 입력에 포함해 후속 질문을 처리한다.
- Agent의 사용자 표시용 텍스트와 구조화된 전체 응답을 함께 보존한다.

## 3. 권장 DB 구조

대화방과 메시지를 분리한 두 개의 테이블을 사용한다.

### `chat_conversations`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | UUID | 대화 ID |
| `user_id` | UUID, FK | 대화 소유자 |
| `title` | VARCHAR(255), nullable | 대화 제목 |
| `created_at` | TIMESTAMPTZ | 생성 시각 |
| `updated_at` | TIMESTAMPTZ | 수정 시각 |

### `chat_messages`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | UUID | 메시지 ID |
| `conversation_id` | UUID, FK | 소속 대화 ID |
| `role` | VARCHAR(20) | `user` 또는 `assistant` |
| `content` | TEXT | 대화에 사용할 텍스트 |
| `payload` | JSONB | 이미지 정보 또는 Agent 전체 응답 |
| `created_at` | TIMESTAMPTZ | 생성 시각 |
| `updated_at` | TIMESTAMPTZ | 수정 시각 |

`content`와 `payload`를 분리하는 이유는 다음과 같다.

- `content`: 채팅 UI 표시와 모델 대화 내역 구성에 사용한다.
- `payload`: 추천 상품 목록, 스타일 가이드, 이미지 URL 등 현재 `AgentResponse` 전체 결과를 손실 없이 저장한다.

예시:

```text
role=user
content="검은색 재킷에 어울리는 바지 추천해줘"
payload={"image_urls": ["..."]}

role=assistant
content="검은색 재킷에는 회색 와이드 슬랙스를 추천합니다."
payload={"status": "success", "recommendations": [...], "style_guide": {...}}
```

## 4. SQLAlchemy 모델 예시

`app/db/models.py`에 다음 모델을 추가한다.

```python
from sqlalchemy import CheckConstraint, Index


class ChatConversation(Base, TimestampMixin):
    __tablename__ = "chat_conversations"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"),
        index=True,
    )
    title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_chat_messages_role",
        ),
        Index(
            "ix_chat_messages_conversation_created_at",
            "conversation_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    conversation: Mapped[ChatConversation] = relationship(
        back_populates="messages",
    )
```

MVP에서는 `created_at ASC, id ASC`로 메시지를 정렬하고, 프론트엔드에서 같은 대화에 대한 동시 전송을 막는다. 한 대화에서 동시 요청까지 지원해야 한다면 별도의 `sequence` 컬럼과 순서 할당 로직을 추가한다.

## 5. API 설계

### 대화 생성

```http
POST /api/v1/chats
```

응답 예시:

```json
{
  "id": "conversation-uuid",
  "title": null,
  "created_at": "2026-08-23T08:00:00Z"
}
```

### 대화 목록 조회

```http
GET /api/v1/chats
```

로그인한 사용자가 소유한 대화만 반환한다.

### 메시지 목록 조회

```http
GET /api/v1/chats/{conversation_id}/messages?limit=50
```

메시지가 많아질 수 있으므로 cursor 기반 페이지네이션을 권장한다.

### 메시지 전송

```http
POST /api/v1/chats/{conversation_id}/messages
```

요청 예시:

```json
{
  "query": "조금 더 저렴한 제품으로 추천해줘",
  "image_urls": []
}
```

응답 예시:

```json
{
  "conversation_id": "conversation-uuid",
  "user_message_id": "user-message-uuid",
  "assistant_message_id": "assistant-message-uuid",
  "response": {
    "status": "success",
    "message": "가격이 낮은 제품을 중심으로 다시 추천했습니다.",
    "recommendations": [],
    "style_guide": null
  }
}
```

클라이언트가 `role`이나 `user_id`를 지정하도록 하지 않는다. `role`은 서버가 결정하고, `user_id`는 액세스 토큰의 `current_user.id`만 사용한다.

## 6. 메시지 처리 흐름

한 번의 메시지 요청은 다음 순서로 처리한다.

1. `conversation_id`가 존재하고 현재 사용자의 대화인지 확인한다.
2. 이전 메시지 중 최근 10~20개를 시간순으로 조회한다.
3. 현재 사용자 질문을 `role=user`로 저장하고 커밋한다.
4. DB 트랜잭션을 닫은 상태에서 Agent를 호출한다.
5. Agent 응답을 `role=assistant`로 저장하고 커밋한다.
6. 사용자 메시지 ID, Agent 메시지 ID, Agent 응답을 반환한다.

외부 LLM 호출 중에는 DB 트랜잭션을 열어두지 않는다. 모델 응답이 오래 걸리면 DB 연결과 락이 불필요하게 유지될 수 있기 때문이다.

서비스 구현 형태는 다음과 같다.

```python
async def send_message(
    db: AsyncSession,
    user_id: str,
    conversation_id: str,
    query: str,
    image_urls: list[str],
) -> dict:
    conversation = await get_owned_conversation(
        db=db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if conversation is None:
        raise ChatNotFoundError

    previous_messages = await load_recent_messages(
        db=db,
        conversation_id=conversation_id,
        limit=20,
    )

    user_message = ChatMessage(
        conversation_id=conversation_id,
        role="user",
        content=query,
        payload={"image_urls": image_urls},
    )
    db.add(user_message)
    await db.commit()

    agent_response = await recommendation_service.create(
        query=query,
        user_id=user_id,
        image_urls=image_urls,
        chat_history=serialize_history(previous_messages),
    )

    assistant_message = ChatMessage(
        conversation_id=conversation_id,
        role="assistant",
        content=agent_response["message"],
        payload=agent_response,
    )
    db.add(assistant_message)
    await db.commit()

    return {
        "conversation_id": conversation_id,
        "user_message_id": user_message.id,
        "assistant_message_id": assistant_message.id,
        "response": agent_response,
    }
```

Agent 호출이 실패하더라도 이미 커밋된 사용자 질문은 유지한다. 실패한 요청을 자동 재시도할 필요가 생기면 추후 `chat_turns` 또는 `status` 컬럼을 추가해 `pending`, `completed`, `failed` 상태를 관리한다.

## 7. Agent 파이프라인 연결

DB에 메시지를 저장하는 것만으로는 연속 대화가 되지 않는다. 조회한 내역을 실제 Agent 입력에 전달해야 한다.

### Agent State 확장

`app/agent/state.py`:

```python
class AgentState(TypedDict, total=False):
    query: str
    chat_history: list[dict[str, str]]
```

### Pipeline 입력 확장

`app/agent/agent_pipeline.py`의 `run()`에 `chat_history`를 추가한다.

```python
async def run(
    self,
    query: str,
    user_id: str,
    chat_history: list[dict[str, str]] | None = None,
    # 기존 인자 생략
) -> dict:
    state: AgentState = {
        "query": query,
        "user_id": user_id,
        "chat_history": chat_history or [],
        # 기존 state 생략
    }
```

`RecommendationService.create()`와 `create_and_persist()`도 `chat_history`를 받아 Pipeline에 전달하도록 수정한다.

### Gemini 입력 구성

`app/services/llm_service.py`의 Gemini 요청 `contents`에 이전 메시지를 추가한다.

```python
contents = [
    {
        "role": "user" if item["role"] == "user" else "model",
        "parts": [{"text": item["content"]}],
    }
    for item in chat_history
]

contents.append(
    {
        "role": "user",
        "parts": [{"text": json.dumps(current_prompt, ensure_ascii=False)}],
    }
)
```

Agent 답변의 전체 `payload`를 그대로 프롬프트에 넣으면 토큰 사용량이 커질 수 있다. 모델에 전달할 때는 다음 정보만 추려서 사용하는 것을 권장한다.

- 사용자 질문
- Agent 답변 요약
- 이전 추천 상품의 `item_id`, 이름, 가격, 카테고리
- 이전 스타일 가이드 요약

## 8. 후속 질문 처리를 위한 Query Rewrite

단순히 최종 LLM 프롬프트에 과거 메시지를 추가하는 것만으로는 다음과 같은 후속 질문의 검색 정확도가 떨어질 수 있다.

```text
이전 질문: 검은색 재킷에 어울리는 바지 추천해줘
이전 답변: 회색 와이드 슬랙스를 추천
현재 질문: 그중 더 저렴한 걸로 보여줘
```

현재 Pipeline은 먼저 LLM으로 일반 대화와 패션 서비스 요청을 분류한다. 패션 서비스 요청이면 VLM 결과와 최근 대화, 현재 질문을 query rewrite LLM에 전달해 `그중` 같은 참조를 독립적인 검색 문장으로 해소한다. 이어 retrieval planner LLM이 직전 RAG 후보의 재사용 여부, 검색 대상과 후보 범위(`shown`, `unseen`, `all`)를 함께 결정한다. 서버는 planner 결과를 다시 검증해 `unseen` 범위에서 이미 노출된 상품을 강제로 제외한다.

```text
변환된 검색 문장:
검은색 재킷에 어울리는 회색 와이드 슬랙스 중 더 저렴한 상품 추천
```

권장 사용 방식:

- 원문 `query`: 사용자 답변 생성과 DB 저장에 사용한다.
- `resolved_query`: intent 분류, RAG 검색, 랭킹에 사용한다.

운영 모드에서는 별도의 query rewrite LLM 호출을 사용한다. `USE_MOCK_AI=true`인 로컬/mock 모드에서만 최근 사용자 질문과 Agent 답변, VLM 메타데이터를 결정론적으로 결합한다.

## 9. 보안 및 운영 기준

- 모든 대화 조회 조건에 `ChatConversation.user_id == current_user.id`를 포함한다.
- 다른 사용자의 대화에는 `404 Not Found`를 반환해 리소스 존재 여부도 노출하지 않는다.
- 클라이언트가 보낸 `user_id`는 신뢰하지 않는다.
- 클라이언트가 임의의 `assistant` 메시지를 저장할 수 없도록 한다.
- 프롬프트에는 전체 내역이 아니라 최근 메시지 또는 토큰 제한 내의 내역만 포함한다.
- DB에는 전체 내역을 보관하고, 모델 입력만 제한한다.
- 중복 요청 방지가 필요하면 `client_message_id`를 받아 사용자별 unique constraint를 추가한다.
- 대화 삭제 API를 제공한다면 소유권 확인 후 cascade 방식으로 메시지를 삭제한다.

## 10. 마이그레이션

로컬 환경에서는 `scripts/init_db.py`의 `Base.metadata.create_all()`로 새 테이블을 생성할 수 있다. 기존 운영 DB에는 `create_all()`만 의존하지 말고 Alembic migration으로 다음 항목을 반영한다.

1. `chat_conversations` 생성
2. `chat_messages` 생성
3. 외래 키와 role check constraint 생성
4. `(conversation_id, created_at)` 인덱스 생성

## 11. 예상 파일 변경 범위

| 파일 | 변경 내용 |
| --- | --- |
| `app/db/models.py` | 채팅 대화 및 메시지 모델 추가 |
| `app/schemas/chat.py` | 요청·응답 Pydantic 스키마 추가 |
| `app/services/chat_service.py` | 대화 생성, 조회, 메시지 저장 로직 추가 |
| `app/api/v1/chats.py` | 채팅 API 추가 |
| `app/api/v1/router.py` | 채팅 Router 등록 |
| `app/agent/state.py` | `chat_history` 및 필요 시 `resolved_query` 추가 |
| `app/agent/agent_pipeline.py` | 대화 내역 입력 전달 |
| `app/agent/nodes.py` | query rewrite 및 history 전달 |
| `app/services/llm_service.py` | Gemini 요청에 과거 대화 포함 |
| `tests/test_chat_service.py` | 저장, 조회, 소유권 테스트 |
| `tests/test_chat_api.py` | API 계약 및 인증 테스트 |

## 12. 테스트 체크리스트

- 대화 생성 시 현재 사용자 ID가 저장된다.
- 사용자 질문과 Agent 답변이 각각 한 건씩 저장된다.
- Agent 전체 응답이 `payload`에 보존된다.
- 메시지가 생성 순서대로 조회된다.
- 다른 사용자의 대화를 조회하거나 메시지를 추가할 수 없다.
- Agent 호출 실패 시에도 사용자 질문은 남는다.
- 최근 메시지 개수 제한이 적용된다.
- 이전 대화가 Agent 입력에 실제로 포함된다.
- `그중 더 저렴한 것`과 같은 후속 질문이 이전 추천을 기준으로 처리된다.
- 기존 추천 API 응답 계약이 변경되지 않는다.

## 13. 구현 순서

1. DB 모델과 migration을 추가한다.
2. 채팅 스키마와 `ChatService`를 구현한다.
3. 대화 생성·목록·메시지 조회 API를 구현한다.
4. 메시지 전송 시 user/assistant 메시지를 저장한다.
5. 최근 메시지를 Pipeline과 LLM에 전달한다.
6. 후속 질문 정확도를 확인하고 필요 시 query rewrite를 추가한다.
7. 소유권, 실패 처리, 대화 순서 테스트를 작성한다.

이 구조로 구현하면 저장 기능과 연속 대화 기능을 분리하면서도, 현재 추천 Agent 응답 계약을 그대로 유지할 수 있다.
