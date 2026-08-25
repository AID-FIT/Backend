# 로컬은 통과하는데 배포만 죽는다

테스트 295개가 전부 통과하는데 프로덕션에서는 추천이 0건이었다. 에러 로그도 없었다.
원인을 찾는 데 오래 걸렸고, 그 사이 원인이 아닌 것을 두 번 되돌렸다.

**핵심 한 줄** — 로컬과 배포가 다른 지점은 코드가 아니라 **환경**이다.
파이썬 버전, 기본값, 접속 문자열. 셋 다 테스트가 볼 수 없는 곳에 있다.

---

## 1. 파이썬 버전이 애노테이션을 가렸다 ⭐

### 증상

`app/services/catalog_matching.py`를 추가한 뒤 무신사 검색이 전부 실패했다.
사용자에게는 "추천 상품 검색 중 오류가 발생했습니다"로만 보였다.

로컬에서는 재현되지 않았다. **테스트 295개가 전부 통과했다.**

### 원인

모듈이 `Optional`을 import하지 않고 애노테이션에 쓰고 있었다.

```python
from typing import Any        # Optional이 없다

def clean_value(value: Any) -> Optional[str]:   # 332, 348, 355, 362행
```

로컬은 **Python 3.14**다. [PEP 649](https://peps.python.org/pep-0649/)로 애노테이션이
지연 평가되므로 정의되지 않은 이름을 써도 import가 통과한다.
배포는 **Python 3.12**다. 함수 정의 시점에 평가하므로 `NameError`로 죽는다.

애노테이션은 주석이 아니라 실행되는 코드다. 그리고 파급이 컸다.

```
catalog_matching (죽음)
   ↑ import
pgvector_rag_service (같이 죽음)
   ↑ import
rag_service._search_pgvector → RAG_SEARCH_FAILED
   ↑
무신사 검색 = 홈 피드 + 채팅 추천 전부
```

테스트도 이 틈을 그대로 통과했다. 관련 테스트 세 개가 수집 단계에서 죽어
CI에 "실패"가 아니라 "에러"로 뜬다 — 놓치기 쉬운 형태다.

### 해결

`Optional[str]`을 `str | None`으로 바꿨다. 나머지 코드베이스와 같은 표기고,
**import가 필요 없어 같은 실수가 재발하지 않는다.**

더 중요한 것은 재발 방지다. 로컬 파이썬이 배포보다 앞서 있는 한 이 부류는 계속 생긴다.
`app/` 전 모듈에 `typing.get_type_hints`를 강제로 돌리는 테스트를 붙였다.

```python
@pytest.mark.parametrize("module_name", app_modules())
def test_annotations_resolve_on_older_pythons(module_name: str) -> None:
    module = importlib.import_module(module_name)
    ...
            try:
                typing.get_type_hints(target)
            except NameError as error:
                unresolved.append(f"{name}: {error}")
```

버그를 되돌려 넣어 실제로 잡히는 것을 확인했다.

> 테스트가 통과한다는 것은 **로컬 런타임에서** 통과한다는 뜻이다.
> 배포 런타임과 버전이 다르면 그 차이만큼 테스트가 보지 못하는 영역이 생긴다.

---

## 2. 예외를 삼켜 아무 기록도 없었다

### 증상

Vercel 런타임 로그에 **에러가 한 줄도 없다.** 그런데 추천은 0건이다.

### 원인

`app/agent/nodes.py`에 로거가 아예 없었다. 모든 노드가 이런 형태다.

```python
except Exception:
    state["error"] = build_error("RAG_SEARCH_FAILED", "추천 상품 검색 중 오류가 발생했습니다.", True, "rag")
```

트레이스백 없이 문구만 바꾼다. 실패는 하는데 **아무도 원인을 볼 수 없다.**

### 로그에서 읽어 낸 것

로거가 없어도 남는 것이 있었다. httpx가 외부 호출을 기록한다.
스트림을 잡아 두고 홈을 열어 보니 이렇게 나왔다.

| 관찰 | 의미 |
| --- | --- |
| `generateContent` 200 OK × 2 | intent·refine 통과 |
| `batchEmbedContents` **0회** | pgvector 검색이 시작조차 안 됨 |
| 에러 로그 0건 | 예외를 삼키는 자리에서 죽음 |

**있는 것이 아니라 없는 것이 단서였다.** 임베딩 호출이 없다는 사실이
"파이프라인이 임베딩 직전에 조용히 죽는다"로 좁혀 줬다.

### 해결

예외를 삼키는 8곳에 트레이스백 로깅을 넣었다.

```python
except Exception:
    # 트레이스백 없이 문구만 바꾸면 배포 후 원인을 볼 방법이 없다.
    logger.exception("agent node failed: %s", "RAG_SEARCH_FAILED")
    state["error"] = build_error(...)
```

사용자에게 보여 줄 문구를 다듬는 것과 원인을 기록하는 것은 다른 일이다. 둘 다 해야 한다.

---

## 3. 동작할 수 없는 값이 기본값이었다

`app/core/config.py`에 이렇게 적혀 있었다.

```python
rag_vector_backend: str = "static"
```

static 경로는 `rag_service_final`을 import하고, 그 파일은 `chromadb`를 import한다.
그런데 **`requirements.txt`에 chromadb가 없다.** 카탈로그 데이터(`data/`)도
`.vercelignore`로 배포 번들에서 빠진다.

| `RAG_VECTOR_BACKEND` | 결과 |
| --- | --- |
| 설정 안 함 (기본값 `static`) | `ModuleNotFoundError` → 모든 무신사 검색 실패 |
| `pgvector` | 정상 |

**배포에서 동작할 수 없는 값이 기본값**이라는 것은, 환경변수를 하나 빠뜨린 새 환경이
조용히 전부 실패한다는 뜻이다. 동작하는 쪽을 기본값으로 바꿨다.

> 기본값은 "가장 안전한 값"이 아니라 **"설정을 잊었을 때 실제로 도는 값"**이어야 한다.
> 안 도는 기본값은 설정 누락을 침묵으로 바꾼다.

---

## 4. `DATABASE_URL`에 결함이 둘 있었다

### 증상

인증 단계에서 터진다. RAG와 무관하다.

```
asyncpg.exceptions.ConnectionDoesNotExistError: connection was closed in the middle of operation
[SQL: SELECT users.id, ... FROM users WHERE users.id = $1::UUID]
```

### 원인

Vercel 프로덕션 값을 직접 확인했다.

```
postgresql+asyncpg://postgres:***@db.<ref>.supabase.co:5432/postgresd
```

**결함 1 — DB 이름이 `postgresd`.** `postgres`의 오타다.

**결함 2 — Supabase 직결 주소는 IPv6 전용이다.**

```
db.<ref>.supabase.co has IPv6 address 2406:da12:...
```

IPv4 환경에서는 `No route to host`로 아예 닿지 않는다.
게다가 `DB_USE_PGBOUNCER=true`라 코드는 pooler를 전제하고 있었다
(`NullPool` + `statement_cache_size=0`). 설정과 접속 대상이 어긋나 있었다.

### 해결

pooler 주소로 바꿨다. **바꾸기 전에 교정본으로 실제 접속해 검증했다** —
`current_database()`가 `postgres`, `product_vectors` 12,794행.

```
postgresql+asyncpg://postgres.<ref>:***@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres
```

| | 이전 | 이후 |
| --- | --- | --- |
| 사용자 | `postgres` | `postgres.<ref>` |
| 호스트 | 직결 (IPv6 전용) | pooler (IPv4) |
| 포트 | 5432 | 6543 (트랜잭션 pooler) |
| DB | `postgresd` | `postgres` |

**환경변수는 재배포해야 적용된다.** 값을 바꾸고 끝내면 아무것도 달라지지 않는다.

> 스킴도 확인한다. `postgresql://`에 `+asyncpg`가 빠지면 SQLAlchemy가 psycopg2를 찾다
> `ModuleNotFoundError`로 죽는다. 엔진을 모듈 로드 시점에 만들므로 앱이 통째로 안 뜬다.

---

## 5. 진단 순서

이번에 **실제로 효과가 있었던** 순서다. 앞의 두 단계를 건너뛰어 시간을 버렸다.

1. **배포된 커밋을 먼저 확인한다.** 고쳤다고 생각한 코드가 안 떠 있을 수 있고,
   다른 PR이 그 위에 머지돼 있을 수 있다. `vercel inspect`, `git ls-remote`.
2. **런타임 로그에서 *없는* 호출을 찾는다.** 에러가 없다고 정상인 게 아니다.
   있어야 할 외부 호출이 없으면 그 직전에서 죽은 것이다.
3. **예외를 삼키는 자리가 있는지 본다.** 로그가 조용하면 코드가 조용한 것이다.
4. **운영 DB·환경변수에 직접 붙는다.** `vercel env ls`(이름), `vercel env pull`(값),
   pooler로 psql/asyncpg 접속. 여기까지 와야 추정이 측정으로 바뀐다.

### 하지 말아야 했던 것

원인을 확정하지 않은 채 "이게 원인일 것"이라는 판단으로 프로덕션 코드를 되돌렸고,
"배포 후 확인해 보세요"라고 넘겼다. 그 판단은 [11 §4](./11-search-layer-gaps.md)에서
실측으로 반증됐다. **검증 수단이 없는 가정은 고칠 근거가 되지 못한다.**

---

## 체크리스트

"로컬에서는 되는데요"가 나올 때 확인한다.

- [ ] 로컬 파이썬 버전과 배포 런타임 버전이 같은가 — 다르면 애노테이션·문법 차이를 의심한다
- [ ] 테스트가 "실패"가 아니라 **"수집 에러"**로 뜨고 있지 않은가
- [ ] 예외를 삼키는 `except`에 트레이스백 로그가 있는가
- [ ] 로그에 **있어야 할 호출이 없지** 않은가 (에러가 없는 것과 정상인 것은 다르다)
- [ ] 기본값이 배포 환경에서 실제로 동작하는 값인가
- [ ] 접속 문자열의 스킴·호스트·포트·DB 이름을 눈으로 확인했는가
- [ ] 그 호스트가 IPv6 전용은 아닌가
- [ ] 환경변수를 바꾸고 **재배포**했는가
- [ ] 배포된 커밋이 내가 고친 그 커밋이 맞는가

---

## 관련 문서

- [01 배포·인프라](./01-deployment-infra.md) — 함수 리전, `maxDuration`
- [02 데이터베이스](./02-database.md) — pgbouncer, 커넥션 풀, 연결 문자열
- [06 운영·보안](./06-ops-security.md) — 환경변수 반영, 시크릿
- [11 검색 계층 누락](./11-search-layer-gaps.md) — 이번에 함께 드러난 검색 결함들
