# 데이터베이스 트러블슈팅

Supabase PostgreSQL을 서버리스 백엔드에서 쓰며 겪은 문제.

**핵심 한 줄** — 서버리스는 커넥션을 재사용할 수 없다는 전제에서 출발해야 하고, pooler는 그 대가로 prepared statement를 포기한다.

---

## 1. pgbouncer Transaction 모드는 prepared statement를 지원하지 않는다

### 증상

Supabase Transaction pooler(포트 `6543`)로 붙이면 SQLAlchemy 쿼리가 실패한다.

### 원인

두 가지 제약이 동시에 걸린다.

1. **Transaction 모드 pooler는 prepared statement를 지원하지 않는다.** asyncpg는 기본적으로 prepared statement를 쓴다.
2. **서버리스는 인스턴스가 다수 뜬다.** 인스턴스별 커넥션 풀을 두면 DB 커넥션 한도를 빠르게 소진한다.

### 해결

`DB_USE_PGBOUNCER=true`일 때 두 동작을 함께 끈다.

```python
# app/db/session.py
engine = create_async_engine(
    settings.database_url,
    poolclass=NullPool,                    # 인스턴스별 풀을 두지 않는다
    connect_args={
        "statement_cache_size": 0,         # prepared statement 비활성화
        "prepared_statement_cache_size": 0,
    },
)
```

### 대가

`NullPool`은 **요청마다 새 커넥션을 연다.** 커넥션 수립 비용이 그대로 응답 시간에 더해진다.

이 비용은 **DB와 같은 리전에 있을 때는 미미하지만, 대륙을 건너면 치명적**이다. 실제로 함수가 미국 동부, DB가 서울일 때 DB 조회 1회가 3.2초까지 늘었다.
→ [배포·인프라 문서 §1](./01-deployment-infra.md)

---

## 2. 비밀번호 특수문자를 URL 인코딩하지 않아 연결이 깨졌다

### 증상

Supabase가 생성한 연결 문자열을 그대로 넣었더니 asyncpg가 호스트를 잘못 파싱했다.

### 원인

비밀번호에 `/`와 `$`가 섞여 있었다. 연결 문자열은 URI라 이런 문자가 구분자로 해석된다. 특히 `/`는 경로 구분자여서 호스트/DB 파싱이 통째로 어긋난다.

### 해결

퍼센트 인코딩한다.

```
원본:   2nFuCTN/xa.V$T2
인코딩: 2nFuCTN%2Fxa.V%24T2
```

| 문자 | 인코딩 |
| --- | --- |
| `/` | `%2F` |
| `$` | `%24` |
| `@` | `%40` |
| `:` | `%3A` |
| `#` | `%23` |
| `?` | `%3F` |

최종 형태:

```
postgresql+asyncpg://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres
```

> Supabase 대시보드의 **Connect → Direct → Transaction pooler**에서 URI를 가져온다.
> `postgresql://` → `postgresql+asyncpg://`로 바꿔야 SQLAlchemy async가 인식한다.

---

## 3. 채팅 메시지 정렬은 `created_at`만으로 부족하다

### 문제

대화 내역을 `created_at`으로만 정렬하면 **같은 시각에 저장된 메시지의 순서가 흔들린다.** 사용자 질문과 AI 답변이 뒤집혀 보일 수 있다.

### 해결

정렬과 커서 모두 `(created_at, id)` 복합 키를 쓴다.

```python
query = query.order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
```

커서 페이지네이션도 같은 복합 키로 이어 읽는다.

```python
query = query.where((ChatMessage.created_at, ChatMessage.id) > (created_at, message_id))
```

조회 경로에 맞춰 복합 인덱스를 뒀다.

```python
Index("ix_chat_messages_conversation_created_at", "conversation_id", "created_at")
```

---

## 4. 외부 LLM 호출 중에 트랜잭션을 열어두지 않는다

### 문제

채팅 메시지 처리는 `사용자 질문 저장 → Agent 호출 → 답변 저장` 순서다.
이걸 한 트랜잭션으로 묶으면 **Gemini 응답을 기다리는 8~12초 동안 DB 트랜잭션과 락을 잡고 있게 된다.**

### 해결

Agent 호출 **전에** 커밋한다.

```python
db.add(user_message)
await db.commit()          # 여기서 트랜잭션을 닫는다

agent_response = await recommendation_service.create(...)   # 외부 호출

db.add(assistant_message)
await db.commit()
```

부수 효과로, Agent 호출이 실패해도 **이미 커밋된 사용자 질문은 남는다.** 대화 맥락이 유실되지 않는다.

---

## 5. UUID 표기 불일치

### 증상

같은 이미지가 최초 업로드냐 중복 응답이냐에 따라 **다른 문자열로 보였다.**

```
신규 응답: aa8cf9caefb646e597f931a203360bff      (uuid4().hex — 대시 없음)
중복 응답: aa8cf9ca-efb6-46e5-97f9-31a203360bff  (Postgres가 정규화 — 대시 있음)
```

### 원인

신규 생성 시 `uuid4().hex`를 그대로 응답에 실었는데, DB에서 읽어온 값은 Postgres가 표준 형식으로 정규화한 것이었다.

### 해결

생성 시점부터 표준 형식을 쓴다.

```python
"id": str(uuid4())   # uuid4().hex 가 아니라
```

> 클라이언트가 응답의 id를 저장해 두는 구조라면 이런 불일치가 **캐시 미스나 중복 항목**으로 이어진다.

---

## 체크리스트

- [ ] pooler를 쓴다면 `NullPool` + prepared statement 비활성화를 했는가
- [ ] 연결 문자열의 비밀번호를 URL 인코딩했는가
- [ ] `postgresql+asyncpg://` 스킴을 썼는가
- [ ] 시간 기반 정렬에 tie-breaker가 있는가
- [ ] 외부 API 호출 구간이 트랜잭션 안에 들어가 있지 않은가
- [ ] 생성한 ID의 표기가 DB에서 읽은 값과 일치하는가

---

### 관련 문서

- [배포·인프라](./01-deployment-infra.md) — 리전 불일치가 커넥션 비용을 증폭시킨 사례
- [AI 연동](./04-ai-integration.md) — 외부 호출과 요청 수명 분리
