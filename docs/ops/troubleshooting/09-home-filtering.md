# 홈 필터 트러블슈팅 — 칩 한 번에 에이전트 한 번

홈 카테고리 칩(`상의`·`바지`·`아우터`…)이 **누를 때마다 추천을 통째로 다시 받고 있었다.**
장애는 아니다. 결과도 맞다. 다만 칩 하나에 13초와 Gemini 호출 한 번이 들었다.

**핵심 한 줄** — 카테고리는 `product_vectors.category`에 대한 `WHERE` 절이다.
**DB 조건절 하나를 13초짜리 LLM 파이프라인에 태우고 있었다.**

관련: [08 추천 품질](./08-recommendation-quality.md)의 §3(카테고리 분산)·§5(홈 검색)·§7(새로고침 회전)에서
이어진다. 이 문서는 그 구조를 필터 중심으로 다시 세운 기록이다.

---

## 1. 칩을 누를 때마다 추천을 새로 받고 있었다

### 증상

카테고리를 하나씩 훑어보면 매번 스켈레톤이 뜨고 13초를 기다렸다. "필터"라기보다
**요청 일곱 번**에 가까웠다. 사용자가 "부담스럽다"고 표현한 지점이다.

### 원인 — 성격이 다른 두 조건을 같은 취급했다

`HomeScreen`의 칩은 `category`를 쿼리스트링에 실어 보냈고, 백엔드는 그것을
`context["category"]`에 넣어 파이프라인을 처음부터 다시 돌렸다.

```
칩 클릭 → GET /recommendations/home/stream?category=바지
       → intent → refine → plan → search → rank → compose   (~13초, Gemini 1회)
```

문제는 홈이 받는 두 조건의 성격이 전혀 다르다는 것이다.

| 조건 | 정체 | AI가 필요한가 |
| --- | --- | --- |
| `prompt` ("비 오는 날 입을 옷") | 자연어 요청 | **필요하다.** 해석해야 검색어가 된다 |
| `category` ("바지") | 카탈로그 열거값 | **필요 없다.** `WHERE category = '바지'`다 |

`category`는 이미 받아 둔 타일에서 걸러 낼 수 있는 값이었다. 그런데 `prompt`와 나란히
놓여 있다는 이유로 같은 경로를 탔다.

### 곁가지 — 새로고침 예산이 이 구멍으로 새고 있었다

홈에는 세션당 5회 + 5분 쿨다운의 새로고침 제한이 있다. 그런데 검색·필터는
**"사용자가 명시적으로 요청한 것"이라 예산을 소모하지 않게** 해 뒀다(의도한 설계였다).

두 결정이 겹치자 칩 여섯 개를 훑는 것만으로 **예산 차감 없이 Gemini 호출 여섯 번**이
나갔다. 비용 상한이 사실상 없었다.

> 예산을 면제할 때는 **그 경로가 정말 싼지** 확인해야 한다. "사용자가 의도했다"는
> 것과 "비용이 안 든다"는 것은 다른 이야기다.

### 해결 — 칩을 클라이언트 필터로 내린다

프론트가 `category`를 **아예 보내지 않는다.** 칩은 이미 받아 둔 `products`를 거른다.

```tsx
const visibleProducts = useMemo(() => (
  selectedCategories.length === 0
    ? products
    : products.filter((product) => selectedCategories.includes(product.category))
), [products, selectedCategories]);
```

백엔드의 `category` 파라미터는 **지우지 않았다.** 사용자가 검색창에 "바지"라고 치면
`infer_target_category`가 질의에서 뽑아 필터로 박는 경로가 살아 있어야 한다
([08 §1](./08-recommendation-quality.md)). 프론트가 안 보낼 뿐이다.

---

## 2. 그런데 8칸으로는 거를 것이 없다

### 증상

칩을 로컬 필터로 바꾸자마자 드러났다. 홈은 타일을 **8개**만 받는데
(`_HOME_TILE_COUNT = 8`) 카테고리는 7종이다. 게다가 `_spread_by_category`가
라운드로빈으로 섞어 놓아([08 §3](./08-recommendation-quality.md)) **카테고리당 평균 1개**였다.
"모자"를 누르면 타일 하나가 나온다.

로컬 필터는 **피드가 충분히 클 때만** 성립한다.

### 첫 번째 안이 틀렸다 — LLM에 36개를 쓰게 하면 안 된다

`_HOME_TILE_COUNT`를 36으로 올리는 것이 가장 단순한 답으로 보였다. 하지만
그 숫자는 Gemini가 이유를 작성해야 하는 상품 수와 프롬프트 크기를 직접 결정한다.

```python
candidate_items = self._candidate_items(ranked_items, max_recommendations)
```

36을 요구하면 **후보 36건을 프롬프트에 싣고, 이유 36개를 생성**해야 한다. 입력과
출력이 크게 늘어난다. 코드 랭커가 순서를 확정하더라도 설명 생성 비용은 그대로다.

> 개수를 늘리는 상수가 **어디까지 전파되는지** 먼저 따라간다. 이 상수는 타일 수인
> 동시에 LLM이 이유를 써야 하는 상품 수다.

### 해결 — 타일을 두 층으로 나눈다

모든 타일에 LLM이 쓴 이유가 필요한 것은 아니다. 상수를 성격에 따라 쪼갠다.

```python
# LLM이 이유까지 써 주는 큐레이션 타일 수. 피드 맨 앞에 놓인다.
_HOME_CURATED_COUNT = 8
# 홈 피드가 싣는 전체 타일 수.
_HOME_FEED_SIZE = 36
```

- **앞 8칸** — LLM이 고르고 이유를 쓴다. **이전과 완전히 같은 호출이다.**
- **뒤 28칸** — 이미 뽑아 둔 `ranked_items`를 카테고리 순환으로 붙인다.
  pgvector 검색 결과를 그대로 싣는 것이라 **LLM 호출이 늘지 않는다.**

```python
def _fill_home_feed(response: dict, ranked_items: list[dict]) -> dict:
    recommendations = list(response.get("recommendations") or [])
    if response.get("status") != "success" or len(recommendations) >= _HOME_FEED_SIZE:
        return response
    ...
    # 점수순으로만 자르면 상위가 한 종류로 쏠려 다른 칩을 눌렀을 때 결과가 비어 버린다.
    while buckets and len(recommendations) < _HOME_FEED_SIZE:
        for category in list(buckets):
            ...
```

재료를 얻으려면 파이프라인이 고르고 **남긴** 것이 필요하다. `/home`은 트레이스를 받는다.

```python
trace = await RecommendationService().create(**request["run_kwargs"], return_trace=True)
result = _fill_home_feed(trace["response"], trace.get("ranked_items") or [])
```

### 함정 (1) — 스트리밍 경로를 같이 안 고치면 절반만 고쳐진다

홈에는 `/home`과 `/home/stream` 두 경로가 있다. 브라우저는 스트리밍을,
네이티브는 폴백을 탄다([08 §8](./08-recommendation-quality.md)). **한쪽만 채우면
플랫폼마다 피드 크기가 달라져 칩이 한쪽에서만 걸린다.** 두 경로 모두에 회귀 테스트를 붙였다.

### 함정 (2) — 지어낸 이유를 달지 않는다

채워 넣은 타일은 `reason: ""`으로 내려간다. `ProductCard`는 빈 이유를 그리지 않고,
프론트가 AI 배지도 붙이지 않는다.

```tsx
// 이유가 붙은 타일만 AI가 직접 고른 것이다.
aiRecommended: Boolean(item.reason),
```

검색·랭킹이 실은 상품에 "AI가 골랐다"고 배지를 다는 것은 거짓말이다.

### 함정 (3) — 스키마가 요구하는 값이 랭킹 결과에 없을 수 있다

`RecommendationItem`은 `source == "musinsa"`이면 `product_url`을 요구한다.
**하나라도 비면 응답 전체가 검증에서 떨어져 홈이 통째로 실패한다.** 채우기 단계에서 거른다.

```python
if not image_url or (source == "musinsa" and not product_url):
    return None
```

---

## 3. 후보 풀을 키우자 새로고침이 다시 망가졌다

### 증상

피드를 36칸 채우려면 후보 풀도 커야 해서 `_HOME_CANDIDATE_POOL`을 30 → 100으로 올렸다.
그러자 **새로고침이 두 화면을 왕복하기 시작했다.** 짝수 번째 새로고침은 처음 본 그 타일이다.

### 원인 — 상한이 걸려 회전할 자리가 사라졌다

[08 §7](./08-recommendation-quality.md)에서 새로고침은 후보 풀 안에서 시작점을 옮기는
방식으로 고쳤다.

```python
start = (refresh_seed * max(limit, 1)) % len(candidates)
```

훑는 후보 수에 상한이 있다는 것이 문제였다.

```python
candidate_limit = min(max(limit * CANDIDATE_MULTIPLIER, limit), MAX_CANDIDATES)  # MAX = 200
```

`limit`이 30일 때는 120건을 훑어 **30씩 네 창**으로 깔끔히 나뉘었다. `limit`이 100이 되자
`100 * 4 = 400`이 **200에서 잘려**, 200 안에서 100씩 회전했다. `start`는 0 → 100 → 0 → 100을
반복한다. 창이 두 개뿐이다.

| 설정 | 훑는 후보 | 새로고침 1·2·3회차 새 상품 비율 |
| --- | ---: | --- |
| 이전 (`pool=30`, `cap=200`) | 120 | 100% · 100% · 100% |
| **풀만 키웠을 때** (`pool=100`, `cap=200`) | 200 | 100% · **0%** · 100% |
| 이후 (`pool=100`, `cap=400`) | 400 | 100% · 100% · 100% |

### 해결 — 상한을 올리고, 공식을 함수로 꺼낸다

`MAX_CANDIDATES`를 400으로 올렸다. 더 중요한 것은 **이 관계를 테스트할 수 있게 만든 것**이다.
인라인 수식으로 두면 다음에 풀 크기를 바꿀 때 또 조용히 깨진다.

```python
def candidate_limit_for(limit: int) -> int:
    """뽑을 개수보다 넉넉히 훑는다.

    새로고침은 이 후보 안에서 시작점을 옮겨 새 상품을 보여준다(`_rotate`).
    훑는 수가 뽑는 수의 배수가 아니면 회전한 창이 서로 겹쳐, 새로고침해도
    본 상품이 다시 올라온다.
    """
    return min(max(limit * CANDIDATE_MULTIPLIER, limit), MAX_CANDIDATES)
```

```python
def test_a_home_sized_pool_still_rotates_into_fresh_products() -> None:
    ...
    assert window(0).isdisjoint(window(1))
    assert window(1).isdisjoint(window(2))
```

> **개수 상수는 혼자 서 있지 않다.** 이 값 하나가 프롬프트 크기(§2)와 새로고침 다양성(§3)을
> 동시에 건드렸다. 늘리기 전에 그 상수를 읽는 곳을 전부 찾는다.

---

## 4. 프론트에서 함께 맞춘 것

| 항목 | 이전 | 이후 |
| --- | --- | --- |
| 선택 방식 | 라디오 (하나만) | **다중 선택.** `상의` + `아우터`를 같이 본다 |
| 칩 개수 표시 | 없음 | **`모자 5`.** 누르기 전에 결과를 안다. 0이면 비활성 |
| 카테고리 목록 | 6종 | **7종.** `원피스/스커트`가 빠져 있었다 |
| "적용된 조건" 건수 | 서버가 준 `result_count` | **화면에 걸린 개수.** 칩을 누르면 바로 줄어든다 |
| 캐시 키 | `prefix:{category}:{prompt}` | **`prefix:{prompt}`** |
| 칩 배치 | 한 줄 (넘침) | 가로 스크롤 (`원피스/스커트` 추가로 8개가 됐다) |

### 칩 목록이 백엔드와 어긋나 있었다

프론트 주석은 "백엔드 `_HOME_CATEGORIES`와 같은 목록이다"라고 적혀 있었지만
**실제로는 달랐다.** 백엔드에는 `원피스/스커트`가 있는데 칩이 없었다. 그 카테고리의 옷은
피드에 올라와도 걸러 볼 방법이 없었다.

주석으로 "같아야 한다"고 적어 두는 것은 동기화가 아니다. 테스트로 박았다.

```tsx
it('offers every category the catalog actually has', async () => {
  const tree = await mount();
  expect(() => chip(tree, '원피스/스커트')).not.toThrow();
});
```

### 캐시 키가 조합 폭발에서 벗어났다

이전 키는 조건마다 항목을 따로 뒀다([08 §5](./08-recommendation-quality.md)). 다중 선택으로
가면서 카테고리를 키에 남겼다면 **2⁷ 조합**이 되어 캐시가 사실상 무용지물이 될 뻔했다.
카테고리가 요청 조건에서 빠지자 키는 `prompt` 하나로 줄었다.

### 새 피드에 없는 카테고리는 선택에서 뺀다

검색으로 피드가 바뀌면 고른 칩이 그 피드에 없을 수 있다. 그대로 두면
**타일이 하나도 없는 화면**이 된다.

```tsx
// 새 피드에 없는 카테고리가 골라진 채로 남으면 타일이 하나도 없는 화면이 된다.
useEffect(() => {
  setSelectedCategories((current) => {
    const available = current.filter((category) => categoryCounts.has(category));
    return available.length === current.length ? current : available;
  });
}, [categoryCounts]);
```

---

## 측정값

| 항목 | 이전 | 이후 |
| --- | ---: | ---: |
| 카테고리 칩 응답 | 13초 (에이전트 재실행) | **즉시** (로컬 필터) |
| 칩 6개를 훑을 때 Gemini 호출 | 6회 | **0회** |
| 홈 진입 1회당 Gemini 호출 | 최대 7회 | **1회** |
| 홈 피드 타일 수 | 8 | **36** |
| 카테고리당 평균 타일 | 약 1개 | **약 5개** |
| LLM이 이유를 쓰는 타일 | 8 | 8 (변화 없음) |
| 후보 풀 / 훑는 후보 | 30 / 120 | **100 / 400** |
| 카테고리 칩 | 6종 (단일 선택) | **7종 (다중 선택 + 개수)** |

테스트: 백엔드 269 통과, 프론트 70 통과.

---

## 주의 — 배포 순서

**백엔드를 먼저 배포해야 한다.** 프론트만 나가면 칩은 8칸 위에서만 걸려,
대부분의 카테고리가 비활성으로 보인다. 반대 순서(백엔드 먼저)는 안전하다 —
구버전 프론트는 36칸을 그냥 다 그린다.

---

## 체크리스트

필터·개수와 관련된 것을 건드릴 때 확인한다.

- [ ] 이 조건은 **AI가 해석해야 하는 값인가, 열거값인가** — 열거값이면 서버로 보낼 이유가 있는지 다시 본다
- [ ] 클라이언트에서 거를 계획이라면 **피드가 카테고리 수 × 최소 한 줄** 이상인가
- [ ] 개수 상수를 늘리기 전에 **그 값을 읽는 곳을 전부** 찾았는가 (프롬프트 크기? 회전 폭?)
- [ ] `/home`과 `/home/stream`처럼 **경로가 둘인 기능을 한쪽만** 고치고 있지 않은가
- [ ] 비용 예산을 면제한 경로가 **정말 싼가**
- [ ] 채워 넣은 데이터에 **AI가 만든 것처럼 보이는 표시**를 달고 있지 않은가
- [ ] 채워 넣는 항목이 응답 스키마의 필수 필드를 **전부 갖고 있는가** (하나가 비면 응답 전체가 떨어진다)
- [ ] 필터 조합이 늘어날 때 **캐시 키가 조합 폭발**하지 않는가
- [ ] 프론트 목록과 백엔드 열거값이 같다는 것이 **주석이 아니라 테스트로** 박혀 있는가
- [ ] 결과 집합이 바뀔 때 **더 이상 유효하지 않은 선택**을 정리하는가

---

## 관련 문서

- [08 추천 품질](./08-recommendation-quality.md) — §3 카테고리 분산, §5 홈 검색, §7 새로고침 회전
- [07 벡터 검색](./07-vector-search.md) — pgvector 후보 풀과 상한
- [05 프론트엔드·UI](./05-frontend-ui.md) — React 19 테스트, 레이아웃 측정
