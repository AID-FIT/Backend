# 벡터 검색 트러블슈팅

로컬 ChromaDB를 Supabase pgvector로 옮기며 겪은 문제.

**핵심 한 줄** — 벡터 DB를 서버리스에 올리려면 데이터보다 **임베딩 모델을 어떻게 부를지**가 먼저 정해져야 한다.

---

## 1. 벡터 DB가 배포본에서 아예 쓰이지 않고 있었다

### 발견

12,794건이 색인된 ChromaDB가 저장소에 있는데, 프로덕션에서 **"와이드 슬랙스 바지"를 물으면
티셔츠가 나왔다.**

### 확인 방법

내장 정적 카탈로그의 `item_id` 목록과 프로덕션 응답을 대조했다.

```
정적 카탈로그 12건: 6081171 6075610 6103287 6125368 ...
프로덕션 응답:      6103287, 6075610, 6084669   ← 전부 정적 카탈로그 안
```

**반환된 id가 전부 12건 안에 있었다.** 12,794건은 한 번도 쓰이지 않았다.

> 기능이 "동작은 하는데 품질이 이상하다"면, **응답이 어느 데이터 소스에서 왔는지**부터 확인한다.
> id 대조가 로그보다 빨랐다.

### 원인

`sentence-transformers`(질의 임베딩에 필요)가 **PyTorch를 끌고 온다.** Vercel 함수 크기 한도는
250MB인데 PyTorch만으로 그 몇 배다. 게다가 `data/chromadb_final` 102MB가 배포 번들에
그대로 실려 있었다.

---

## 2. 임베딩 모델이 이전의 진짜 제약이다

### 함정

"벡터를 그대로 옮기면 된다"고 생각하기 쉽다. 하지만 **질의도 같은 모델로 벡터화해야
검색이 성립한다.** 저장된 벡터가 `jhgan/ko-sroberta-multitask`(768차원)라면 질의도 그 모델이
필요하고, 그 모델은 서버리스에서 못 돈다.

### 선택지

| 방식 | 장점 | 대가 |
| --- | --- | --- |
| **Gemini 임베딩으로 재색인** | HTTP 호출이라 서버리스에서 동작. 기존 `GEMINI_API_KEY` 재사용 | 재색인 시간·비용. 검색 품질이 달라질 수 있음 |
| 기존 벡터 유지 | 재색인 없음. 품질 동일 | 질의 임베딩용 별도 서비스(Cloud Run 등)가 필요 — 인프라가 하나 늘어남 |

이 프로젝트는 **재색인**을 택했다. 인프라를 늘리지 않는 쪽이 운영 부담이 작다.

---

## 3. HNSW 인덱스는 2000차원까지만 지원한다

`gemini-embedding-001`의 기본 출력은 3072차원인데, **pgvector의 HNSW 인덱스 상한은 2000**이다.
그대로 쓰면 인덱스를 만들 수 없다.

`outputDimensionality`로 잘라 쓴다. 이 프로젝트는 **1536**을 쓴다.

```python
{
    "content": {"parts": [{"text": text}]},
    "taskType": "RETRIEVAL_DOCUMENT",
    "outputDimensionality": 1536,
}
```

12,794건 × 1536차원 × 4바이트 ≈ 78MB. Supabase에서 충분히 감당된다.

---

## 4. 색인과 질의의 `taskType`을 나눠야 한다

Gemini 임베딩은 용도에 따라 다른 벡터를 만든다. 같은 값을 쓰면 검색 품질이 떨어진다.

| 용도 | `taskType` |
| --- | --- |
| 상품 색인 | `RETRIEVAL_DOCUMENT` |
| 사용자 질의 | `RETRIEVAL_QUERY` |

---

## 5. ⚠️ 대량 색인에 재시도와 이어받기가 없으면 중간에 멈춘다

### 무슨 일이 있었나

12,794건 색인이 **6,000건에서 멈췄다.** 임베딩 API 연결이 끊겼는데 —

- **오류 메시지가 비어 있었다.** `httpx` 연결 오류는 `str(exc)`가 빈 문자열인 경우가 있다.
- **재시도가 없어** 한 번의 끊김이 전체를 중단시켰다.
- **이어받기가 없어** 다시 돌리면 6,000건을 처음부터 또 임베딩해야 했다.

### 해결

**재시도** — 끊김·429·5xx는 지수 백오프로 재시도하고, 4xx는 즉시 올린다.
기다려도 안 풀리는 걸 붙잡고 있으면 원인만 가려진다.

```python
except httpx.HTTPError as exc:
    # 메시지가 비는 경우가 있어 타입을 함께 남긴다
    raise EmbeddingError(f"...: {type(exc).__name__}: {exc}", retryable=True) from exc

if response.is_error:
    retryable = response.status_code == 429 or response.status_code >= 500
```

**이어받기** — 이미 적재된 `item_id`를 건너뛴다.

```python
existing = await conn.execute(text(f"SELECT item_id FROM {TABLE}"))
already = {row[0] for row in existing}
records = [r for r in records if r["item_id"] not in already]
```

> 수천 건 규모의 배치 작업에는 **재시도와 이어받기를 처음부터 넣는다.**
> 없으면 실패할 때마다 전체 비용을 다시 낸다.

---

## 6. chromadb 없이 원본을 읽는다

마이그레이션 스크립트가 `chromadb`를 import하면 그 무거운 의존성을 설치해야 한다.
**`chroma.sqlite3`를 직접 읽으면 필요 없다.**

메타데이터는 키-값 행으로 쪼개져 있어 `embedding_id` 기준으로 다시 모은다.

```sql
SELECT e.embedding_id, m.key,
       COALESCE(m.string_value, CAST(m.int_value AS TEXT), CAST(m.float_value AS TEXT)) AS value
FROM embeddings e
JOIN embedding_metadata m ON m.id = e.id
WHERE e.segment_id = ?
```

---

## 7. 검색 설계에서 정한 것들

**후보를 넉넉히 뽑는다** — 메타데이터 가중치를 얹으면 벡터 순위와 최종 순위가 달라진다.
요청 개수의 4배(상한 200)를 뽑아 두고 상위만 돌려준다.

**가격이 비어 있으면 통과시킨다** — 가격 필터에서 `NULL`을 제외하면 결과가 지나치게 줄어든다.

```sql
(price IS NULL OR price <= :price_max)
```

**`unisex` 요청은 성별을 좁히지 않는다** — 좁히면 대부분의 상품이 빠진다.

---

## 8. 결과

### 검색 품질

| 질의 | 이전 (정적 12건) | 이후 (pgvector 12,794건) |
| --- | --- | --- |
| 와이드 슬랙스 바지 | **티셔츠 3건** | 투턱/원턱 와이드 슬랙스 |
| 여름에 시원한 반팔 | — | 쿨링·COOLMAX 반팔 |
| 5만원 이하 바지 | — | 전부 바지, 전부 5만원 이하 |

후속 질문도 정상이다. "더 저렴한 걸로" → 맥락(와이드 슬랙스)을 유지한 채 9,900원까지 내려갔다.

### 응답 시간 — pgvector는 병목이 아니다

| 구간 | 시간 |
| --- | ---: |
| 질의 임베딩 | 0.58초 |
| 벡터 검색 (12,794건) | 0.3~1.2초 |
| **pgvector 합계** | **~1초** |
| 전체 응답 | 15~18초 |

나머지는 순차로 도는 **Gemini 호출 4번**이다. 벡터 검색을 옮긴 것과 무관하다.
→ [AI 연동](./04-ai-integration.md)

---

## 9. 저장소 정리

`data/chromadb_final`(102MB)을 git 추적에서 뺐다.

```bash
git rm -r --cached data/chromadb_final
```

**두 가지를 알아둔다.**

- 이 커밋을 받으면 **로컬 `data/chromadb_final`이 삭제된다.** 로컬에서 chroma 경로를 쓰던
  사람은 다시 받아야 한다.
- **git 히스토리의 102MB는 그대로 남아 clone 크기는 줄지 않는다.** 실제로 줄이려면
  `git filter-repo` 같은 히스토리 재작성이 필요하고, 모든 팀원이 다시 clone해야 한다.

기존 chroma 경로는 코드에 남겨 뒀다. `RAG_VECTOR_BACKEND=static|chroma|pgvector`로 고르고,
**프로덕션만 `pgvector`**다. 로컬·K3s 흐름은 그대로 동작한다.

---

## 체크리스트

- [ ] 질의 임베딩 모델을 배포 환경에서 부를 수 있는가 (로컬 모델은 서버리스에 못 올린다)
- [ ] 임베딩 차원이 인덱스 상한(HNSW 2000) 안에 드는가
- [ ] 색인과 질의의 `taskType`을 나눴는가
- [ ] 대량 배치에 재시도와 이어받기가 있는가
- [ ] 마이그레이션 스크립트가 무거운 의존성 없이 원본을 읽는가
- [ ] 필터가 결과를 지나치게 줄이지 않는가 (NULL 처리)
- [ ] 검색 품질을 **데이터 소스 확인**으로 검증했는가 (id 대조)

---

### 관련 문서

- [AI 연동](./04-ai-integration.md) — LLM 체인과 응답 시간
- [데이터베이스](./02-database.md) — pgvector가 올라간 Supabase 설정
- [배포·인프라](./01-deployment-infra.md) — 함수 크기와 번들 제외
