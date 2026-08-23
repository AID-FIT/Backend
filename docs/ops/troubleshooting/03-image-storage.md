# 이미지 스토리지 트러블슈팅

Supabase Storage 연동과 내용 해시 기반 중복 제거에서 겪은 문제.

**핵심 한 줄** — 내용 해시로 경로를 정하면 중복이 사라지는 대신 **파일이 사용자 간에 공유되고**, 삭제가 위험해진다.

---

## 1. `Invalid Compact JWS` — 신형 키는 `apikey` 헤더가 필요하다

### 증상

업로드 시 403이 돌아왔다.

```json
{"statusCode":"403","error":"Unauthorized","message":"Invalid Compact JWS","code":"AccessDenied"}
```

### 원인

`Authorization: Bearer <key>`만 보냈는데, 키가 **신형 `sb_secret_...` 형식**이었다.
Storage가 이를 JWT로 파싱하려다 실패한 것이다. 구형 `service_role` 키는 JWT라 통과하지만 신형 키는 JWT가 아니다.

에러 메시지의 "JWS"가 힌트였다 — JWT 파싱 단계에서 터졌다는 뜻이다.

### 해결

`apikey` 헤더를 함께 보낸다. **구형 키도 두 헤더를 함께 받으므로** 양쪽 모두 동작한다.

```python
headers = {
    "apikey": settings.supabase_service_key,
    "Authorization": f"Bearer {settings.supabase_service_key}",
}
```

### 주의

`anon` / `publishable` 키로는 업로드되지 않는다. **RLS에 막힌다.**
반드시 `secret` 또는 `service_role` 키를 쓰고, **클라이언트에 절대 노출하지 않는다.**

---

## 2. 내용 해시로 중복 제거

### 문제

같은 사진을 다시 올리면 세 가지가 중복됐다.

- 스토리지에 같은 바이트가 다시 저장
- 옷장 항목이 한 벌 더 생성
- VLM 분석이 다시 실행 (비용 + 시간)

### 해결

**저장 경로를 내용의 SHA-256으로 정했다.**

```python
content_hash = hashlib.sha256(content).hexdigest()
file_name = f"{content_hash}{suffix}"
```

같은 이미지는 항상 같은 경로가 되므로:

| 단계 | 최적화 |
| --- | --- |
| 스토리지 | 업로드 전 `HEAD`로 존재 확인 → 있으면 **전송 자체를 건너뜀** |
| DB | 경로가 결정적이라 **URL로 기존 행을 조회** → 행 생성·분석 건너뜀 |
| 동시성 | 그사이 같은 객체가 생겨 409가 나면 기존 객체를 그대로 사용 |

### 검증 결과

| 요청 | 결과 |
| --- | --- |
| 1회차 | 새 URL 생성 |
| 2회차 (동일 파일) | **같은 URL, 같은 ID** — 새 행 없음 |
| 3회차 (다른 이미지) | 다른 URL |
| 목록 조회 | 3장이 아닌 **2장** |

---

## 3. ⚠️ 삭제할 때 참조 카운팅이 필요하다

**이 문서에서 가장 중요한 항목이다.**

### 함정

내용 해시로 경로를 정하면 **같은 사진을 올린 서로 다른 사용자가 하나의 파일을 공유하게 된다.**

삭제 요청이 왔을 때 무작정 원본을 지우면 → **남의 옷장 사진이 깨진다.**

### 해결

같은 URL을 참조하는 이미지가 남아 있지 않을 때만 원본을 지운다.

```python
storage_url = image.storage_url
await db.execute(delete(ClosetItem).where(ClosetItem.image_id == image.id))
await db.delete(image)
await db.flush()

# 아직 참조가 남아 있으면 원본을 지우지 않는다
remaining = await db.execute(
    select(func.count()).select_from(ImageAsset)
    .where(ImageAsset.storage_url == storage_url)
)
if remaining.scalar_one() == 0:
    await StorageService().delete_by_url(storage_url)
```

### 검증 결과

| 시나리오 | 원본 파일 | 비고 |
| --- | --- | --- |
| 두 사용자가 같은 사진 업로드 | 경로 공유 확인 | |
| user1 삭제 | **살아있음 (200)** | user2 목록 정상 |
| user2 삭제 (마지막 참조) | **제거됨 (400)** | |
| 남의 사진 삭제 시도 | — | 404 |

### 부수 결정

스토리지 삭제 실패는 삼킨다. **원본이 남는 것보다 사용자의 삭제 요청이 실패하는 쪽이 더 나쁘다.**

```python
except httpx.HTTPError:
    return   # 삭제 요청 자체를 실패시키지 않는다
```

> 삭제 직후 public URL이 잠시 200을 반환할 수 있다. **CDN 캐시**이며 시간이 지나면 만료된다.

---

## 4. webp / heic가 파일 선택창에 안 잡혔다

### 증상

webp 파일을 올릴 수 없다는 보고.

### 원인 추적

백엔드를 먼저 의심했지만, **실제로 검증해 보니 백엔드는 이미 정상이었다.**

실제 webp를 업로드한 결과:

- `.webp` 확장자로 저장됨
- Supabase가 `content-type: image/webp`로 서빙
- VLM의 `mime_type.startswith("image/")` 검사 통과
- **Gemini Vision이 분석까지 완료**

문제는 프론트엔드 파일 선택창이었다.

```ts
input.accept = 'image/*';   // OS의 MIME 등록에 의존한다
```

`image/*`는 브라우저가 OS의 MIME 데이터베이스를 참조해 필터링한다. 환경에 따라 **webp나 heic가 선택 목록에 나타나지 않는다.**

### 해결

실제로 받는 형식을 **확장자까지 함께** 명시한다.

```ts
input.accept = [
  'image/png', 'image/jpeg', 'image/webp', 'image/heic', 'image/heif',
  '.png', '.jpg', '.jpeg', '.webp', '.heic', '.heif',
].join(',');
```

Gemini Vision이 지원하는 형식은 png / jpeg / webp / heic / heif다.
heic를 포함시켜 **아이폰 사진도 바로 올라간다.**

### 교훈

"백엔드가 X를 지원하지 않는다"는 신고를 받으면 **백엔드를 고치기 전에 백엔드가 정말 못 하는지 확인한다.**
이번 건은 백엔드가 멀쩡했고 입력 경로가 막혀 있었다.

---

## 5. Public 버킷의 노출 범위

현재 `uploads` 버킷은 **public**이다. URL을 아는 사람은 인증 없이 이미지를 볼 수 있다.

- URL이 랜덤이 아니라 **내용 해시**라는 점에 유의한다. 같은 이미지를 가진 사람은 URL을 계산할 수 있다.
- 옷 사진은 개인 사진에 가깝다.

**미해결 과제.** private 버킷 + signed URL로 바꿀 수 있으나, URL에 만료가 생겨 클라이언트 재발급 처리가 필요하다.

---

## 체크리스트

- [ ] Storage 인증에 `apikey` 헤더를 함께 보내는가
- [ ] `secret` / `service_role` 키를 쓰고 클라이언트에 노출하지 않는가
- [ ] 내용 해시로 경로를 정한다면 **삭제 시 참조 카운팅**을 하는가
- [ ] 스토리지 삭제 실패가 사용자 요청을 실패시키지 않는가
- [ ] 파일 선택창의 `accept`가 실제 지원 형식과 일치하는가
- [ ] 버킷 공개 범위가 데이터 민감도에 맞는가

---

### 관련 문서

- [배포·인프라](./01-deployment-infra.md) — 서버리스에 디스크가 없는 이유
- [AI 연동](./04-ai-integration.md) — 해시를 이용한 분석 결과 재사용
