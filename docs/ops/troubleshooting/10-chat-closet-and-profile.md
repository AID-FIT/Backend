# 추천 탭 옷장 연동·프로필 확장 트러블슈팅 — 컬럼 하나가 서비스를 멈춘다

추천 탭에 "옷장에서 옷 가져오기"와 대화 삭제를 넣고, 내정보에 성별·키를 더했다.
기능 자체는 크지 않다. 그런데 세 가지 모두 **이미 돌아가고 있는 것을 건드린다** —
매 턴 옷장 전체를 넘기던 채팅, 전체 교체로 동작하던 프로필 PATCH, 그리고
마이그레이션 단계가 없는 배포 파이프라인이다.

**핵심 한 줄** — K3s를 걷어내면서 `scripts/init_db.py`를 자동 실행해 주던 자리가 사라졌다.
**Vercel 배포에는 DDL을 적용하는 단계가 없으므로, 컬럼 추가는 코드가 아니라 배포 순서 문제다.**

관련: [02 데이터베이스](./02-database.md)의 스키마 운영과, [09 홈 필터](./09-home-filtering.md)의
배포 순서 주의에서 이어진다.

---

## 1. `create_all`은 이미 있는 테이블에 컬럼을 붙이지 않는다

### 함정

`user_preferences`에 `gender`와 `height_cm`을 더했다. 로컬에서는 아무 문제가 없다 —
테이블이 없으면 `Base.metadata.create_all`이 새 정의대로 만들어 주기 때문이다.

운영은 다르다. 테이블이 이미 있으면 `create_all`은 **아무것도 하지 않는다.**
컬럼도, CHECK 제약도 붙지 않는다.

### 왜 위험한가 — 반경이 넓다

SQLAlchemy는 `SELECT`에 컬럼을 하나하나 나열한다. 모델에만 컬럼이 있고 DB에 없으면
그 테이블을 읽는 모든 쿼리가 `UndefinedColumn`으로 떨어진다. `user_preferences`를 읽는 곳은:

| 경로 | 무엇이 멈추나 |
| --- | --- |
| `GET /users/me` | 내정보 화면 |
| `POST /users/me/onboarding/complete` | 신규 가입 전체 |
| `GET /recommendations/home` · `/home/stream` | 홈 피드 |
| `POST /chats/{id}/messages` | 추천 탭 대화 |

**프로필 한 줄을 못 읽어서 앱 전체가 멈춘다.** K3s 시절에는 initContainer가
`scripts/init_db.py`를 매 배포마다 돌려 이 문제가 드러나지 않았다.

### 해결 — DDL을 먼저, 코드를 나중에

```python
# scripts/init_db.py
await connection.exec_driver_sql(
    "ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS gender VARCHAR(20)"
)
# ADD CONSTRAINT에는 IF NOT EXISTS가 없다. DROP 후 ADD라야 몇 번을 돌려도 같은 상태가 된다.
await connection.exec_driver_sql(
    "ALTER TABLE user_preferences DROP CONSTRAINT IF EXISTS ck_user_preferences_gender"
)
await connection.exec_driver_sql(
    "ALTER TABLE user_preferences ADD CONSTRAINT ck_user_preferences_gender "
    "CHECK (gender IS NULL OR gender IN ('men', 'women', 'unisex'))"
)
```

배포 순서를 고정한다.

1. `DATABASE_URL`을 Supabase로 두고 로컬에서 `python scripts/init_db.py`
   (pooler·IPv6로 접속이 안 되면 같은 SQL을 Supabase SQL Editor에 붙여넣는다)
2. 백엔드 배포
3. 프론트 배포

> Alembic이 없는 저장소에서 컬럼을 추가한다는 것은, **배포 파이프라인에 없는 단계를 사람이
> 대신 밟는다**는 뜻이다. 그 단계를 문서가 아니라 순서로 적어 둔다.

---

## 2. 프로필에 필드를 더하자 백엔드 테스트 24개가 한꺼번에 깨졌다

### 증상

`to_agent_profile`에 `gender`/`height_cm`을 더하자마자 채팅과 홈 테스트가 무더기로 떨어졌다.

```
AttributeError: 'StubPreference' object has no attribute 'gender'
app/services/user_service.py:16: AttributeError
```

### 원인 — 테스트 더블이 모델을 흉내 내고 있었다

이 저장소에는 테스트 DB도 `conftest.py`도 없다. 대신 `StubPreference`처럼 손으로 쓴
클래스가 ORM 행 역할을 한다(`tests/test_home_recommendation.py`, `tests/test_home_stream_endpoint.py`,
`tests/test_chat_contracts.py`). 모델이 늘면 더블도 늘어야 한다.

### 해결 — 더블을 고친다. `getattr` 기본값으로 덮지 않는다

`to_agent_profile`을 `getattr(preference, "gender", None)`로 바꾸면 테스트는 즉시 초록이 된다.
그렇게 하지 않았다. **그 방어는 §1의 진짜 위험(운영 DB에 컬럼이 없는 상태)을 가려 주지 못하면서**
— 그건 쿼리 단계에서 터진다 — 모델과 더블이 어긋난 사실만 조용히 덮는다.

> 모델을 흉내 내는 더블은 모델이 바뀔 때 **함께 깨지는 것이 정상 동작**이다.
> 깨지지 않게 만드는 방어는 대개 다른 곳에서 더 늦게 터진다.

---

## 3. 성별 표를 어디에 두느냐가 의존 방향을 뒤집었다

### 증상

카탈로그는 성별을 `men`/`women`/`unisex`로만 쓴다. 화면은 "남성"을 보낸다.
경계에서 맞춰야 하는데, 필요한 표(`GENDER_SYNONYMS`)는 이미 `app/services/vlm_service.py`에 있었다.

`app/schemas/user.py`의 validator에서 그걸 그대로 import하면 **스키마가 서비스를 import한다.**
`app/services/user_service.py`는 반대로 `app/schemas/user.py`를 import하고 있어서, 표를
`user_service.py`로 옮기는 선택지는 순환 import가 된다.

### 원인 — 표의 주인이 잘못돼 있었다

`GENDER_SYNONYMS`는 VLM 응답 정리에만 쓰이던 시절에 `vlm_service.py`에 자리를 잡았다.
이제는 **VLM과 사용자 입력이라는 서로 다른 두 경로**가 같은 정규형을 필요로 한다.
어느 한쪽 서비스에 두면 다른 쪽이 반드시 꼬인다.

### 해결 — 표를 중립 지대로 옮기고 양쪽이 읽는다

```python
# app/core/gender.py
CANONICAL_GENDERS = ("men", "women", "unisex")
GENDER_SYNONYMS = {"남성": "men", "여자": "women", "남녀공용": "unisex", ...}

def normalize_gender(value: object) -> str | None:
    """men/women/unisex 중 하나로 바꾼다. 비었으면 None, 못 알아보면 ValueError."""
```

`vlm_service.py`는 자기 사본을 지우고 이걸 읽는다. `schemas/user.py`는 validator에서 부른다.
DB에도 같은 값만 들어가도록 CHECK 제약을 걸어 세 층이 같은 어휘를 쓴다.

모르는 값은 `None`으로 삼키지 않고 `ValueError`를 던져 422가 되게 했다. 조용히 버리면
**사용자가 성별을 골랐는데도 필터가 걸리지 않는 상태**를 아무도 눈치채지 못한다.

---

## 4. AsyncSession에서 대화를 지우면 `MissingGreenlet`으로 터진다

### 함정

`ChatConversation.messages`에는 `cascade="all, delete-orphan"`이 걸려 있고
`ChatMessage.conversation_id` FK에는 `ondelete="CASCADE"`가 있다. 둘 다 있으니
`await db.delete(conversation)` 한 줄이면 될 것처럼 보인다.

되지 않는다. ORM cascade를 적용하려면 SQLAlchemy가 먼저 `conversation.messages`를 읽어야 하는데,
그 지연 로딩이 async 컨텍스트 밖에서 일어나 `MissingGreenlet`이 된다.

### 해결 — Core `delete()` 두 문장, 메시지가 먼저

```python
# app/services/chat_service.py
if await self.get_owned_conversation(db, conversation_id, user_id) is None:
    raise ChatNotFoundError          # 남의 것도, 없는 것도 똑같이 404

await db.execute(delete(ChatMessage).where(ChatMessage.conversation_id == conversation_id))
result = await db.execute(
    delete(ChatConversation).where(
        ChatConversation.id == conversation_id,
        ChatConversation.user_id == user_id,
    )
)
if result.rowcount == 0:
    # 소유권 확인과 삭제 사이에 다른 요청이 먼저 지웠다.
    await db.rollback()
    raise ChatNotFoundError
await db.commit()
```

세 가지가 여기 함께 들어 있다.

- **메시지를 먼저 지운다.** 대화를 먼저 지우고 중간에 실패하면 주인 없는 메시지가 남는다.
- **`rowcount == 0`이면 되돌린다.** 소유권 확인과 삭제 사이의 틈을 막는다.
- **FK 설정에 기대지 않는다.** 운영 DB의 FK에 `ON DELETE CASCADE`가 실제로 걸려 있는지는
  코드가 확인할 수 없다. 두 문장이면 걸려 있든 아니든 결과가 같다.

> 소유권 검사는 삭제 **전에**, 소유자 조건은 삭제 문장 **안에** — 둘 다 필요하다.
> 앞의 것은 404를 만들고, 뒤의 것은 경합을 막는다.

---

## 5. 옷장에서 고른 옷이 바뀌었는데 지난 후보를 다시 쓰고 있었다

### 함정

채팅은 매 턴 직전 어시스턴트 메시지의 `_agent_context`에서 후보 풀을 되살려 재검색을 건너뛴다
(`ChatService._extract_previous_agent_context`). "그중 더 저렴한 걸로" 같은 후속 질문을
13초짜리 파이프라인 없이 답하기 위한 장치다.

그 후보 풀은 **그때의 옷장 범위로 만들어진 것**이다. 이번 턴에 사용자가 참고할 옷을 바꿔도
질의 문장이 비슷하면 계획 단계가 재사용을 택할 수 있다. 그러면 **재킷을 골라 물었는데
바지를 기준으로 뽑힌 후보가 답으로 나온다.** 오류가 아니라 조용히 틀린 답이다.

### 해결 — 범위를 컨텍스트에 함께 적는다

```python
# app/services/chat_service.py
def closet_scope_key(requested_ids: list[str]) -> str:
    """순서가 달라도 같은 범위이므로 정렬해서 만든다."""
    return ",".join(sorted(requested_ids)) if requested_ids else CLOSET_SCOPE_ALL
```

저장할 때 `closet_scope_key`를 남기고(`schema_version` 3), 읽을 때 이번 턴의 키와 다르면
빈 컨텍스트를 돌려 재검색시킨다. 키가 없는 옛 컨텍스트는 `"all"`로 읽는다 —
선택 기능이 없던 시절에는 실제로 모두 옷장 전체였다.

덕분에 선택 없이 이어가는 대화는 지금까지처럼 재사용되고, **처음 옷을 고르는 턴에서만 한 번**
다시 검색한다.

> 캐시에는 결과만이 아니라 **그 결과를 만든 입력 조건**을 함께 적는다.
> 입력이 하나 늘면 캐시 키도 하나 늘어야 한다.

---

## 6. PATCH가 전체 교체라, 두 필드를 모르는 앱이 그것을 지운다

### 함정

`PATCH /users/me/preferences`는 이름과 달리 전체 교체다. `upsert_preference`가
`styles`·`preferred_colors`·`avoid_items`·`sizes`를 무조건 덮어쓴다.

성별·키에 같은 규칙을 적용하면, **두 필드를 모르는 구버전 앱이 프로필을 저장할 때마다
값이 조용히 지워진다.** 앱 업데이트는 한 번에 오지 않는다.

### 해결 — 본문에 실제로 담겼을 때만 반영한다

```python
# app/services/user_service.py
if "gender" in payload.model_fields_set:
    preference.gender = payload.gender
if "height_cm" in payload.model_fields_set:
    preference.height_cm = payload.height_cm
```

Pydantic v2의 `model_fields_set`은 "기본값이라서 None"과 "명시적으로 None을 보냈다"를 구분한다.
덕분에 `gender: null`을 보내 지우는 것은 그대로 되고, 키를 아예 빼면 보존된다.
기존 필드의 의미는 건드리지 않았다 — 지금 동작에 기대는 클라이언트가 있다.

---

## 7. 프론트 테스트를 쓰다 제품 결함 둘을 찾았다

### 증상 (1) — 삭제에 실패하면 결과를 볼 수 없었다

대화 전체 삭제가 실패했을 때, 무엇이 남았는지 확인할 방법이 없었다.

### 원인 — 실패 경로에서도 목록을 닫고 있었다

`handleDeleteAllConversations`가 요청을 보내기 **전에** `setIsSidebarOpen(false)`를 하고 있었다.
성공하면 자연스럽지만, 실패하면 오류 문구만 남고 목록은 사라진다.

### 해결 — 닫는 것을 성공 이후로 옮긴다

```tsx
// src/screens/recommend/StyleRecommendScreen.tsx
try {
  await deleteAllConversations();
} catch {
  // 실패하면 목록을 그대로 둔다. 무엇이 남았는지 볼 수 있어야 한다.
  setError('대화를 삭제하지 못했어요.');
  return;
}
setIsSidebarOpen(false);
```

### 증상 (2) — 질문을 타이핑하면 고르던 옷이 사라졌다

옷장 피커에서 옷을 고르고, 닫지 않은 채 입력창에 글자를 치면 **선택이 초기화됐다.**

### 원인 — 열 때 한 번 할 일을 매 렌더마다 하고 있었다

```tsx
// 이렇게 두면 안 된다
const [selectedIds, setSelectedIds] = useState(initialSelectedIds);
useEffect(() => {
  setSelectedIds(initialSelectedIds);   // 부모가 리렌더될 때마다 실행된다
}, [initialSelectedIds]);
```

`initialSelectedIds`는 부모가 `selectedClosetItems.map((item) => item.id)`로 **매 렌더마다 새로
만드는 배열**이다. 참조가 매번 달라지니 의존성 배열이 의미를 잃고, effect가 렌더마다 돈다.
피커는 입력 바 위에 얹힐 뿐이라 그 뒤 `TextInput`이 살아 있다. `setDraft`가 부모를 다시 그리고,
그때마다 고르던 선택이 초기값으로 되돌아갔다.

### 해결 — effect를 지운다

피커는 닫을 때 조건부 렌더링에서 빠져 **언마운트된다.** 다시 열면 새로 마운트되므로
`useState(initialSelectedIds)`의 초기값만으로 "지금 고른 것에서 이어서 시작"이 이미 성립한다.
effect는 그 위에 얹은 군더더기였고, 얹는 순간 버그가 됐다.

> 부모가 만들어 넘기는 배열·객체는 **매번 다른 참조**다. 의존성 배열에 그대로 넣으면
> "값이 바뀔 때"가 아니라 "그릴 때마다"가 된다. 마운트마다 한 번이면 되는 일은
> `useState` 초기값으로 충분한지 먼저 본다.

### 곁가지 — 테스트를 쓰다 밟은 것들

- **토글을 헬퍼 안에 숨기지 않는다.** 대화 목록은 좁은 화면에서 토글로 열린다.
  "필요하면 연다"는 헬퍼를 한 테스트에서 두 번 부르면 두 번째가 **닫는다.**
  한 번만 부르도록 테스트 본문에 드러냈다.
- **보간이 섞인 `<Text>`는 children이 배열로 온다.** `대화 {n}개를 모두 지울까요?`는
  `String(children)`으로 읽으면 `대화 ,2,개를…`이 된다. 배열이면 `join('')`으로 합쳐야 한다.
- **`IN` 절의 바인딩 파라미터는 목록 하나로 묶인다.** `{'conversation_id_1': ['c1']}`이지
  `{'conversation_id_1': 'c1'}`이 아니다.

---

## 측정값

| 항목 | 이전 | 이후 |
| --- | ---: | ---: |
| 백엔드 테스트 | 353 | **401** |
| 프론트 테스트 | 70 | **125** |
| 채팅 한 턴에 실리는 옷장 아이템 | 항상 전체 | **고른 만큼 (최대 8)** |
| 프로필에서 검색 조건이 되는 항목 | 선호 스타일 | **선호 스타일 + 성별** |
| 대화 삭제 | 불가 | **1건 · 전체** |
| 옷장 선택이 바뀐 뒤의 후보 재사용 | 그대로 재사용 | **무효화 후 재검색** |

프론트 `tsc --noEmit` 통과.

---

## 주의 — 배포 순서

**DDL → 백엔드 → 프론트.** 이유가 각각 다르다.

- DDL이 백엔드보다 늦으면 §1대로 프로필을 읽는 모든 경로가 멈춘다.
- 프론트가 백엔드보다 이르면 `closet_item_ids`가 422로 막힌다.
  `MessageSendRequest`는 `extra="forbid"`다. 같은 이유로 `UserProfile`과 `AppliedFilters`에도
  필드를 함께 열어야 한다 — 빠뜨리면 `POST /recommendations`와 홈 피드가 422가 된다.

반대로 백엔드만 먼저 나가는 것은 안전하다. 구버전 프론트는 새 필드를 보내지 않고,
새 필드가 없는 요청은 지금까지와 똑같이 동작한다.

---

## 체크리스트

스키마나 오래된 계약을 건드릴 때 확인한다.

- [ ] 추가한 컬럼이 **`create_all`이 아니라 DDL로** 들어가는가 — 배포 전에 적용했는가
- [ ] 그 테이블을 읽는 경로를 **전부** 세어 봤는가 (한 곳이 아니라 앱 전체일 수 있다)
- [ ] `ADD CONSTRAINT`를 **DROP IF EXISTS와 짝지어** 재실행 가능하게 만들었는가
- [ ] 새 필드를 `extra="forbid"` 스키마에 **전부** 열었는가 (요청·응답·에이전트 계약)
- [ ] 전체 교체로 동작하는 PATCH에 필드를 더할 때, **구버전 클라이언트가 그걸 지우지** 않는가
- [ ] 캐시에 **그 결과를 만든 입력 조건**이 함께 적혀 있는가 — 입력이 늘었는데 키가 그대로가 아닌가
- [ ] 키가 없는 옛 캐시를 **어떤 값으로 읽을지** 정했는가
- [ ] AsyncSession에서 ORM cascade에 기대고 있지 않은가 (`MissingGreenlet`)
- [ ] 삭제가 **소유권 확인 전 · 삭제문 안** 양쪽에 소유자를 걸고 있는가
- [ ] 없는 것과 남의 것이 **같은 404**로 답하는가
- [ ] 실패 경로에서 사용자가 **결과를 확인할 화면**을 닫아 버리지 않는가
- [ ] `useEffect` 의존성에 **부모가 매 렌더 새로 만드는 배열·객체**가 들어 있지 않은가
- [ ] 마운트마다 한 번이면 되는 초기화를 **effect가 아니라 `useState` 초기값**으로 할 수 있는가
- [ ] 모델을 흉내 내는 테스트 더블을 **방어 코드로 덮고** 있지 않은가

---

## 관련 문서

- [02 데이터베이스](./02-database.md) — pgbouncer, 커넥션 풀, 정렬 tie-breaker
- [09 홈 필터](./09-home-filtering.md) — 배포 순서, 프론트·백엔드 열거값 일치
- [08 추천 품질](./08-recommendation-quality.md) — §3 카테고리 분산, 실제로 걸리는 검색 필터
- [05 프론트엔드·UI](./05-frontend-ui.md) — react-native-web에 `Alert`가 없다, React 19 테스트
