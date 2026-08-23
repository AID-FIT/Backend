# AI 연동 트러블슈팅

Gemini LLM / Vision을 실제로 켠 뒤 드러난 성능·구조 문제.

**핵심 한 줄** — mock에서 실물로 바꾸는 순간 응답 시간이 60배가 되고, 그동안 숨어 있던 구조 문제가 전부 드러난다.

---

## 1. mock → 실제 전환의 충격

`USE_MOCK_AI=false`로 바꾼 직후 측정값이다.

| 동작 | mock | 실제 Gemini |
| --- | ---: | ---: |
| 추천 1건 | ~0.2초 | **12.2초** |
| 후속 질문 | ~0.2초 | **8.0초** |
| 이미지 업로드 | ~0.2초 | **2.4~5.0초** |

Gemini 3 계열은 답변 전에 추론하는 시간이 있어 느리다.
`GEMINI_TIMEOUT_SECONDS`도 기본 30초로는 부족해 60초로 올렸다.

> mock으로 개발하는 동안에는 **응답 시간에 기댄 설계 결함이 보이지 않는다.** 실물 전환은 빨리, 별도 환경에서라도 해보는 게 좋다.

---

## 2. ⚠️ 업로드가 AI 호출을 동기로 기다리고 있었다

### 증상

업로드가 2.4~5.0초로 느려졌다. 사진을 고른 사용자가 그동안 아무것도 못 한다.

### 원인

`POST /images`가 이 순서로 동작했다.

```
파일 저장 → DB 행 생성 → Gemini Vision 호출 대기 → 응답
                          ^^^^^^^^^^^^^^^^^^^^ 사용자가 여기서 기다린다
```

옷 메타데이터 분석(VLM)이 업로드 요청 안에 들어 있었다.

### 왜 서버 백그라운드 작업을 쓸 수 없었나

FastAPI `BackgroundTasks`나 `asyncio.create_task`가 떠오른다. 하지만 —

> **Vercel 서버리스는 응답을 보낸 뒤 함수를 동결한다.**
> 백그라운드 작업이 완료된다는 보장이 없다.

이건 서버리스 전반의 제약이다. 응답 이후 작업이 필요하면 큐, Cron, 또는 별도 요청이 필요하다.

### 해결 — 분석을 별도 엔드포인트로 분리

```
POST /images              저장 + 행 생성까지만. 즉시 응답 (analyzed: false)
POST /images/{id}/analyze VLM 분석. 이미 분석됐으면 재호출 안 함 (멱등)
```

클라이언트가 업로드 성공 후 **기다리지 않고** `/analyze`를 호출한다.
그 요청은 자체 실행 시간 예산(60초)을 갖는다.

```ts
// 분석 실패는 추천 품질에만 영향을 준다. 업로드 흐름을 막지 않는다.
export function requestAnalysisInBackground(image: UploadedImage): void {
  if (image.analyzed) return;
  void analyzeImage(image.id).catch(() => {});
}
```

응답에 `analyzed` 플래그를 실어 클라이언트가 호출 여부를 판단한다.

---

## 3. 내용 해시로 분석 결과까지 재사용

### 착안

저장 경로가 내용의 SHA-256이라 **URL이 같으면 픽셀이 같다.**
같은 사진을 다시 분석해도 결과가 달라질 이유가 없다.

### 구현

```python
async def reuse_analysis(self, db, user, image) -> ClosetItem | None:
    """같은 내용의 사진이 이미 분석돼 있으면 결과를 복사한다."""
    analyzed = await db.execute(
        select(ClosetItem)
        .join(ImageAsset, ClosetItem.image_id == ImageAsset.id)
        .where(
            ImageAsset.storage_url == image.storage_url,
            ClosetItem.image_id != image.id,
        )
        .limit(1)
    )
    source = analyzed.scalar_one_or_none()
    if source is None:
        return None
    return await self._upsert(db, user, image, dict(source.raw_vlm_result or {}))
```

**다른 사용자가 올린 분석 결과도 재사용한다.**

### 결과

| 상황 | 소요 | Gemini 호출 |
| --- | ---: | --- |
| 신규 사진 업로드 | 0.67~0.95초 | 없음 (분리됨) |
| 이미 분석된 사진을 **다른 사용자**가 업로드 | **0.31초** | **없음 — 결과 복사** |
| `/analyze` 재호출 (이미 분석됨) | 0.44초 | 없음 |

복사본이 원본과 동일한 15개 VLM 필드를 갖는 것을 DB에서 확인했다.

### 종합 개선

| | 이전 | 이후 |
| --- | ---: | ---: |
| 업로드 | 2.4~5.0초 | **0.67~0.95초** (약 3.5배) |

---

## 4. 대화 맥락 전달 — mock에서는 검증되지 않는다

### 배경

LLM은 요청 간 상태를 유지하지 않는다. 후속 질문을 처리하려면 이전 대화를 입력에 다시 넣어야 한다.

`chat_history`를 `ChatService → RecommendationService → AgentPipeline → AgentState → nodes → LlmService → Gemini 프롬프트`까지 관통시켰다.

### 함정

**mock LLM은 `chat_history`를 받고도 무시한다.** 유닛 테스트로 "전달되는지"만 검증할 수 있고, 실제로 맥락이 반영되는지는 알 수 없었다.

실제 Gemini를 켠 뒤에야 처음으로 검증할 수 있었다.

```
1차: "검은색 재킷에 어울리는 바지 추천해줘"
2차: "방금 추천한 것 중에 더 저렴한 걸로 다시 골라줘"

→ "검은색 재킷"을 다시 말하지 않았는데도 맥락 유지
→ 9,900원 → 25,900원 → 29,900원 가격순 선별
```

### 토큰 비용 주의

대화 내역을 넣을 때 **`payload`(추천 상품 목록 전체)는 제외**하고 `role`/`content`만 보낸다.
payload까지 넣으면 상품 목록이 매 턴 반복돼 토큰이 불필요하게 커진다.

```python
def _serialize_history(self, messages):
    return [{"role": m.role, "content": m.content} for m in messages]
```

직전 20개로 제한한다.

---

## 5. 홈 추천 캐시

홈 화면은 진입할 때마다 추천을 호출한다. 12초 대기 + API 비용이 매번 쌓인다.

**클라이언트에 30분 TTL 캐시**를 뒀다. 사용자가 직접 새로고침하기 전까지는 최근 결과를 보여준다.

> 서버리스는 인스턴스 메모리가 유지되지 않아 **서버측 인메모리 캐시는 의미가 없다.**
> 서버에서 캐시하려면 DB나 외부 캐시가 필요하다.

---

## 6. 테스트 더블 시그니처 불일치가 원인을 감췄다

### 증상

`chat_history` 인자를 추가했더니 기존 테스트 11개가 깨졌다. 그런데 에러 메시지가 원인과 무관했다.

```
AssertionError: assert '최종 추천 결과 생성에 실패했습니다.'
              == '최종 추천 결과 형식이 올바르지 않습니다.'
```

### 원인

테스트 더블(`FakeLlmService`)이 새 인자를 못 받아 `TypeError`가 났고, 그게 프로덕션 코드의 **광범위한 `except Exception`에 잡혀** 일반 오류 메시지로 바뀐 것이었다.

```python
except ValidationError:
    state["error"] = build_error("FINAL_RESPONSE_INVALID", ...)
except Exception:          # ← TypeError가 여기 잡혔다
    state["error"] = build_error("FINAL_RESPONSE_FAILED", ...)
```

### 교훈

> 넓은 `except`는 이런 식으로 원인을 감춘다.
> 인터페이스를 바꿀 때는 **테스트 더블도 함께 맞춘다.**

---

## 7. 분석이 도달하지 못한 사진 회수 (해결됨)

### 문제

업로드와 분석을 분리한 대가로, 분석 요청이 도달하지 못하면(네트워크 끊김, 앱 종료)
그 사진이 **메타데이터 없이 남았다.** 추천 품질에만 영향을 주고 사진 자체는 멀쩡하지만,
**회수할 방법이 없었다.**

### 해결 — 두 경로

| 경로 | 언제 | 무엇을 |
| --- | --- | --- |
| `POST /images/analyze-pending` | 옷장 탭 진입 시 | 내 잔여분을 회수. 사용자가 앱을 열면 곧바로 복구된다 |
| `GET /cron/analyze-pending` | 하루 1회 (Vercel Cron) | 사용자 구분 없이 훑는 안전망 |

**한 배치는 3장으로 묶는다.** VLM 한 건이 수 초라 함수 실행 시간 안에 끝나야 한다.
남으면 `has_more`로 알리고 호출부가 이어서 부른다.

**한 장이 실패해도 나머지를 계속 처리한다.** 실패 시 `rollback` 후 다음 장으로 넘어가고,
실패한 장은 다음 호출에서 다시 잡힌다. **이 구조 자체가 재시도가 된다.**

```python
for image in pending:
    try:
        if await self.reuse_analysis_for(db, image.user_id, image) is None:
            await self.analyze_and_store_for(db, image.user_id, image)
        await db.commit()
    except Exception:
        await db.rollback()   # 한 장의 실패가 배치 전체를 되돌리지 않게 끊는다
```

### ⚠️ Cron 엔드포인트는 반드시 잠근다

`CRON_SECRET`이 비어 있으면 **404로 닫는다.** 인증 없는 스윕 엔드포인트는 외부에서 호출해
**AI 비용을 태울 수 있다.** 비교는 타이밍 공격을 피해 `secrets.compare_digest`를 쓴다.

> Vercel Cron은 `CRON_SECRET`이 설정돼 있으면 `Authorization: Bearer <secret>`으로 호출한다.
> 메서드는 **GET**이다.

### 검증 결과

| 검증 | 결과 |
| --- | --- |
| 미분석 3장 → `analyze-pending` | `{analyzed:3, failed:0}` → 미분석 0장 |
| 미분석 2장 → Cron 호출 | `{analyzed:2, failed:0}` → 미분석 0장 |
| Cron, 시크릿 없음 / 잘못됨 | 401 |

---

## 체크리스트

- [ ] 사용자 요청이 외부 AI 호출을 동기로 기다리고 있지 않은가
- [ ] 서버측 백그라운드 작업에 의존하고 있지 않은가 (서버리스는 동결된다)
- [ ] 같은 입력에 대한 결과를 재사용할 수 있는가
- [ ] 프롬프트에 불필요하게 큰 payload를 싣고 있지 않은가
- [ ] 반복 호출되는 화면에 캐시가 있는가
- [ ] mock으로만 검증된 기능이 남아 있지 않은가
- [ ] 인터페이스 변경 시 테스트 더블도 맞췄는가
- [ ] 요청이 중간에 끊겼을 때 남는 작업을 회수할 경로가 있는가
- [ ] 인증 없이 열린 엔드포인트가 AI 비용을 태울 수 있지 않은가

---

### 관련 문서

- [배포·인프라](./01-deployment-infra.md) — `maxDuration`과 실행 시간
- [이미지 스토리지](./03-image-storage.md) — 내용 해시 기반 경로
- [데이터베이스](./02-database.md) — 외부 호출과 트랜잭션 분리
- [벡터 검색](./07-vector-search.md) — 상품 검색과 임베딩
