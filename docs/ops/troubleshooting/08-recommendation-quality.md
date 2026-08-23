# 추천 품질 트러블슈팅 — 카테고리 분산과 홈 검색

검색은 성공하는데 **결과가 한 종류로 쏠리거나 엉뚱한 종류가 나오는** 문제를 모았다.
장애가 아니라 품질 결함이라 에러 로그에 아무것도 남지 않는다. 전부 화면을 보고 발견했다.

**핵심 한 줄** — 벡터 검색은 "비슷한 것"을 준다. 코디 추천이 원하는 것은 **"어울리는 다른 것"**이다.
이 간극을 메우지 않으면 바지 사진에는 바지가, 겨울 취향에는 아우터만 나온다.

---

## 1. "이 바지에 어울리는 상의"에 바지가 나왔다

### 증상

바지 사진을 올리고 "바지와 어울리는 상의 추천해줘"라고 물었더니 이런 답이 왔다.

> 현재 추천 목록에 상의 아이템이 포함되어 있지 않아, 대신 요청하신 블루 하이웨스트 와이드
> 데님과 비슷한 무드로 코디하기 좋은 팬츠 아이템들을 추천해 드립니다

**LLM이 정직했다는 점이 중요하다.** 상의가 없다고 말하고 있다. 즉 문제는 생성이 아니라
**LLM에 넘어간 후보에 상의가 한 벌도 없었다는 것**이다. 이럴 때 프롬프트를 고치면 안 된다.
검색 단계를 봐야 한다.

### 원인 — 두 겹이었다

**(1) VLM이 읽은 카테고리가 검색 필터에 그대로 박혔다.**

`_inferred_vlm_filters`는 사진에서 읽은 속성(색·카테고리·계절)을 검색 필터로 바꾼다.
사진 속 옷이 바지니까 `category=pants`가 필터에 들어가고, **후보가 바지로 고정된다.**
상의를 아무리 요청해도 검색 결과에 상의가 들어올 수 없다.

**(2) 질의에서 카테고리를 뽑는 로직이 pgvector 이전 중에 빠졌다.**

ChromaDB 시절에는 질의에서 목표 카테고리를 읽는 단계가 있었는데, pgvector로 옮기며
그 호출을 옮기지 않았다. 필터를 정정할 유일한 장치가 사라진 상태였다.

> 저장소를 갈아끼울 때 사라지기 쉬운 것은 **저장소 코드가 아니라 그 위에 얹혀 있던
> 보정 로직**이다. 이전 전에 "어댑터 바깥에서 어댑터를 손보던 코드"를 목록으로 만들어 둔다.

### 해결

`app/services/target_category.py`를 두고, 질의에서 **찾는 옷**의 카테고리를 뽑는다.

```python
# "바지에 어울리는 상의 추천해줘" → 추천 앞의 마지막 카테고리는 상의
recommend_pos = query.find("추천")
```

사진 속 옷은 참고 대상이고 찾는 옷은 질의가 정한다. 그래서 질의가 카테고리를 말했다면
**VLM이 추론한 카테고리는 버린다.**

```python
if query_names_a_category(query):
    inferred.pop("category", None)
```

### 여기서 한 번 더 틀렸다

첫 수정은 `category`를 **무조건** 버렸다. 그러자 호출부가 명시적으로 넘긴
`context["category"]`까지 같이 날아갔다. 버려야 하는 것은 **VLM이 추론한 값 하나**다.
그래서 삭제 위치를 `_inferred_vlm_filters`의 결과가 `filters`에 병합되기 **직전**으로 옮겼다.

```python
inferred = self._inferred_vlm_filters(vlm_items)
if query_names_a_category(query):
    inferred.pop("category", None)          # 추론값만 버린다
for key, value in inferred.items():
    if value and key not in filters:        # 명시값은 이미 filters에 있어 덮이지 않는다
        filters[key] = value
```

### 왜 두 값을 비교하지 않는가

"VLM 카테고리와 질의 카테고리가 다르면 버린다"가 자연스러워 보이지만 **불가능하다.**
VLM은 영어(`"top"`)를, 질의 키워드 사전은 한국어(`"상의"`)를 쓴다. 같은 옷이어도 문자열이 다르다.
그래서 판단 기준을 "다른가"가 아니라 **"질의가 말했는가"**로 잡았다.

### 테스트가 버그를 박제하고 있었다

`test_rag_request_adds_profile_and_vlm_filter_candidates`가 질의
`"이 상의와 어울리는 바지 추천해줘"`에 대해 `filters["category"] == "top"`을 **기대**하고 있었다.
구현을 그대로 옮겨 적은 테스트라 버그를 통과시켰다. `"category" not in filters`로 고쳤다.

> 테스트가 구현을 복사하면 회귀는 잡아도 **결함은 못 잡는다.** 단언문에 "사용자가 원한 것"이
> 아니라 "코드가 하는 것"이 적혀 있으면 의심한다.

---

## 2. 홈 타일에 내 옷장 옷이 올라왔다

### 증상

홈은 사러 갈 상품을 보여주는 자리인데 **사용자가 이미 가진 옷**이 타일에 나왔다.

### 원인

`retrieval_planner`(LLM)가 `retrieval_target`을 `closet`으로 골랐다. 옷장 정보가 프롬프트에
들어가니 "옷장에서 찾으라는 뜻"으로 읽을 만했다.

채팅에서는 이 판단이 옳다. "내 옷장에서 찾아줘"가 가능해야 한다. 하지만 **홈에서는 아니다.**

### 해결 — 판단을 없애는 대신 잠근다

호출부가 목표를 고정할 수 있게 `lock_retrieval_target` 플래그를 뒀다.

```python
state["retrieval_target"] = (
    state["recommendation_target"]
    if state.get("lock_retrieval_target")
    else plan.retrieval_target
)
```

홈 엔드포인트만 `lock_retrieval_target=True, recommendation_target="musinsa"`를 넘긴다.
채팅은 기존대로 계획을 따른다.

> LLM 판단이 한 화면에서만 틀린다면 프롬프트를 고치기 전에 **그 화면이 판단을 건너뛸 수
> 있는지** 본다. 프롬프트 수정은 다른 화면까지 흔든다.

---

## 3. 홈 타일이 한 카테고리로만 찼다

### 증상

타일이 1~2개만 보이고, 그나마 **전부 아우터**였다.

### 원인 (1) — 질의가 목표 카테고리를 잘못 알려주고 있었다

`_build_home_query`가 옷장을 요약하며 이런 줄을 넣고 있었다.

```
보유 아이템: 아우터, 바지, 상의
```

의도는 "이런 옷들을 갖고 있으니 참고해라"였다. 그런데 §1에서 만든 `infer_target_category`가
**이 줄의 마지막 카테고리를 찾는 옷으로 읽었다.** 검색이 상의 하나로 좁혀졌다.

`closet_items`는 이미 `state`로 따로 전달된다. **자연어 문장에 다시 적을 이유가 없었다.**
그 줄을 삭제했다.

```python
def test_home_query_does_not_list_owned_categories() -> None:
    assert infer_target_category(home_query()) is None
```

> 프롬프트에 넣는 문장은 **다른 규칙 기반 파서의 입력이기도 하다.** 자연어로 맥락을 덧붙일 때
> 그 문장을 읽는 코드가 또 있는지 확인한다.

### 원인 (2) — 취향이 쏠리면 상위 후보도 쏠린다

옷장이 "겨울 · 검정 · 스트릿"으로 일관되면 벡터 검색 상위 N건이 자연스럽게 아우터로 찬다.
검색은 정상이다. **정확히 요청대로** 비슷한 것을 준 결과다.

점수순으로 상위를 자르면 LLM에 넘어가는 후보가 전부 같은 종류가 된다.

### 해결 — 순위를 카테고리별로 라운드로빈한다

```python
def _spread_by_category(self, items):
    buckets = {}
    for item in items:
        buckets.setdefault(_term(item.get("category")) or "unknown", []).append(item)

    spread = []
    while buckets:
        for category in list(buckets):
            spread.append(buckets[category].pop(0))
            if not buckets[category]:
                del buckets[category]
    return spread
```

각 카테고리의 1등부터 돌아가며 채운다. **버리지 않고 순서만 바꾼다** — 뒤쪽 후보도 그대로 남는다.

```
입력 (점수순):  아우터0.99  아우터0.98  아우터0.97  상의0.80  바지0.70
출력 (분산):    아우터0.99  상의0.80   바지0.70   아우터0.98  아우터0.97
```

**채팅에는 적용하지 않는다.** "바지 추천해줘"는 한 카테고리를 원하는 질의고, 여기서 섞으면
오히려 틀린다. 그래서 `diversify_by_category` 플래그를 홈에서만 켠다.

| 경로 | 정렬 | 이유 |
| --- | --- | --- |
| 홈 타일 | 카테고리 분산 | 코디 한 벌을 보여주는 자리 |
| 채팅 | 점수순 그대로 | 보통 한 카테고리를 지정해 묻는다 |

### 원인 (3) — 후보 풀이 목표 개수와 같았다

후보를 5건만 뽑아 "5개 골라"라고 하면 LLM은 **고르는 게 아니라 그대로 옮겨 적는다.**
분산도 할 여지가 없다. 후보 풀을 30건으로 올렸다.

```python
_HOME_TILE_COUNT = 8
_HOME_CANDIDATE_POOL = 30   # 목표의 2배 이상
```

```python
def test_home_candidate_pool_leaves_room_to_choose() -> None:
    assert _HOME_CANDIDATE_POOL >= _HOME_TILE_COUNT * 2
```

### 결과

| 항목 | 이전 | 이후 |
| --- | --- | --- |
| 타일 수 | 1~2개 | **8개** |
| 카테고리 종류 | 1종 (아우터) | **4~5종** (아우터·바지·모자·가방·신발) |
| 옷장 아이템 노출 | 있음 | **없음** |

---

## 4. 추천 개수 상한이 화면 간에 공유돼 있었다

### 증상

홈 후보 풀을 30으로 올렸는데도 타일이 **5개에서 멈췄다.**

### 원인

`llm_service.py`의 모듈 상수 하나가 모든 화면의 상한이었다.

```python
MAX_RECOMMENDATIONS = 5
```

프롬프트의 목표 개수, 응답 정규화 시 자르는 개수, mock 응답 개수가 전부 이 값을 봤다.
후보를 아무리 늘려도 마지막에 5개로 잘렸다.

> "개수를 늘렸는데 안 늘어난다"면 파이프라인 **가장 뒤쪽의 상한**부터 본다. 앞에서 늘린 값은
> 뒤의 자르기를 이기지 못한다.

### 해결 — 상수를 기본값으로 낮추고 호출부가 정하게 한다

```python
async def compose_recommendation(self, ..., max_recommendations: int | None = None):
    limit = max(1, max_recommendations or MAX_RECOMMENDATIONS)
```

`None`이면 기존 동작 그대로다. **채팅은 한 줄도 바뀌지 않는다.**

값이 실제로 끝까지 닿아야 하므로 경로 전체에 인자를 뚫었다.

```
홈 엔드포인트 → RecommendationService.create → AidFitAgentPipeline.run
  → AgentState["max_recommendations"] → final_response_node
  → LlmService.compose_recommendation → _build_gemini_payload
```

중간 한 곳만 빠뜨려도 조용히 5개로 돌아간다. 그래서 **끝단에서 확인하는 테스트**를 뒀다.

```python
def test_requested_tile_count_reaches_the_llm() -> None:
    assert run_pipeline(max_recommendations=7)["max_recommendations"] == 7
```

### 두 가지를 더 맞춰야 했다

**(1) LLM에 목표 개수를 말해야 한다.** 후보를 30개 줘도 모델은 서너 개 고르고 끝낸다.
프롬프트에 `target_recommendation_count`를 넣고, 시스템 지시에도 명시했다.

```
Return exactly target_recommendation_count recommendations when candidate_items holds
at least that many suitable products; return fewer only when it does not.
```

**(2) 후보 수가 목표에 따라 늘어야 한다.** `_candidate_items`의 상한도 고정값이었다.

```python
candidate_limit = max(MAX_LLM_CANDIDATES, max_recommendations * 2)
```

### 프론트도 함께 맞췄다

홈은 **2열 그리드**라 홀수면 마지막 줄이 반만 찬다. 목표를 7이 아니라 **8**로 잡았다.

로딩 스켈레톤도 같은 수로 맞췄다. 스켈레톤이 6개인데 결과가 8개면 로딩이 끝나는 순간
목록이 늘어나며 화면이 튄다.

```tsx
// 백엔드 _HOME_TILE_COUNT와 맞춘다
const homeTileCount = 8;
```

> 개수는 백엔드와 프론트 **양쪽에 상수로 존재한다.** 한쪽만 고치면 레이아웃이 흔들린다.
> 서로를 가리키는 주석을 남긴다.

---

## 5. 홈 검색이 검색처럼 동작하지 않았다

홈 상단에는 자유 입력창과 무드 칩(캐주얼·여름·미니멀·데이트룩)이 있다. 여기서 검색하면
결과가 검색어와 무관해 보였다. 원인이 네 개였다.

### (1) 검색어가 문장 맨 뒤 "추가 요구사항:"으로 붙었다

```
20대. 스트릿 캐주얼 스타일. 주요 색상: black. ... 추천해줘. 추가 요구사항: 바지
```

`infer_target_category`는 **"추천" 앞**만 목표 카테고리로 읽는다. "바지"가 뒤에 있으니
목표로 잡히지 않았다. 임베딩에서도 60자 넘는 취향 문장에 두 글자가 묻혔다.

검색어를 문장 **앞**으로 옮겼다.

```
바지. 20대. 스트릿 캐주얼 스타일. ... 이 조건에 맞는 무신사 상품을 추천해줘.
```

> 프롬프트에서 **문장 순서가 파서의 입력이기도 하다.** §3과 같은 함정의 반대 방향이다.

### (2) 카테고리 분산이 검색을 방해했다

§3에서 홈에 켠 `diversify_by_category`가 검색에도 그대로 켜져 있었다.
"바지"를 검색해도 결과가 여러 종류로 섞였다.

분산은 **"아무거나 보여줄 때"** 옳은 보정이다. 종류를 찍어 물으면 틀린다.

```python
diversify_by_category=infer_target_category(query) is None,
```

무드 칩(캐주얼·여름·미니멀·데이트룩)은 종류가 아니라서 `None`이 나오고 분산이 유지된다.

### (3) 칩이 입력창을 덮어썼다

`handleCategoryPress`가 `setQuery(category)`로 입력창을 갈아치우고 있었다.
"바지"를 입력한 뒤 "여름"을 누르면 **방금 적은 요청이 사라졌다.**

둘 다 "추가로 원하는 점"이므로 합쳐서 보낸다. 그리고 같은 칩을 다시 누르면 해제되게 했다
(해제할 방법이 없으면 한 번 누른 뒤로는 기본 추천으로 못 돌아간다).

```tsx
const searchTerm = [selectedCategory, query.trim()].filter(Boolean).join(' ');
```

초기 선택값도 `categories[0]`에서 `''`로 바꿨다. 칩이 켜져 보이는데 결과에 반영되지 않는
상태였다.

### (4) 검색 결과가 기본 홈 캐시를 덮어썼다

홈은 진입할 때마다 Gemini를 부르므로 결과를 30분 캐시한다. 그런데 **검색 결과도 같은 키에
저장**되고 있었다. "바지"를 검색한 뒤 앱을 껐다 켜면, 검색어 없이 들어와도 바지 목록이
되살아났다.

캐시는 기본 추천만 담게 했다.

```tsx
if (!nextQuery.trim()) {
  void writeCache(homeCacheKey, nextProducts);
}
```

> 캐시 키가 하나면 **그 키에 무엇을 넣는지가 곧 의미**가 된다. 파생 결과를 같은 키에 쓰면
> 캐시가 조용히 상태를 오염시킨다.

---

## 체크리스트

추천 결과가 이상할 때 **이 순서로** 확인한다. 뒤에서 앞으로 가면 시간을 버린다.

- [ ] LLM이 "후보에 없다"고 말하는가 → 생성이 아니라 **검색** 문제다
- [ ] 검색 필터에 사진에서 추론한 값이 박혀 있지 않은가
- [ ] 질의가 카테고리를 말했는데 무시되고 있지 않은가
- [ ] 프롬프트에 넣은 자연어 문장을 **다른 파서가 읽고** 있지 않은가
- [ ] 후보 풀이 목표 개수보다 충분히 큰가 (2배 이상)
- [ ] 파이프라인 끝단에 고정 상한이 남아 있지 않은가
- [ ] 이 보정이 **모든 화면에서** 옳은가, 아니면 특정 화면에서만 옳은가
- [ ] 사용자가 입력한 검색어가 다른 값에 덮이거나 문장 끝에 묻히지 않는가
- [ ] 캐시 키 하나에 성격이 다른 결과를 함께 쓰고 있지 않은가
- [ ] 테스트 단언문이 "원한 것"이 아니라 "코드가 하는 것"을 적고 있지 않은가

---

## 관련 문서

- [04 AI 연동](./04-ai-integration.md) — mock→실물 전환, 동기 호출 분리
- [07 벡터 검색](./07-vector-search.md) — pgvector 이전, 이번 §1의 회귀가 발생한 지점
