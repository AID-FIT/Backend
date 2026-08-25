# 운영·보안 트러블슈팅

배포 운영, 시크릿 관리, 협업 과정에서 겪은 문제.

**핵심 한 줄** — 코드가 맞아도 **환경변수 반영 시점**과 **시크릿이 놓인 위치**를 놓치면 그대로 사고가 된다.

---

## 1. 🔐 실제 API 키가 `.env.example`에 들어간 사고

### 무슨 일이 있었나

Gemini API 키가 `.env.example`에 실제 값으로 들어갔다.

**`.env.example`은 git 추적 대상이다.** `.env`와 다르다.

```
.gitignore:
  .env          ← 무시됨
  (.env.example은 없음 → 추적됨)
```

### 결과

커밋 전에 발견해 **유출은 없었다.** `git log -S`로 이력에 없음을 확인했다.

```bash
git log --oneline -S "AQ.Ab8RN6LX" -- .env.example   # 결과 없음
```

하지만 그대로 커밋했다면 GitHub에 올라갔다.

### 재발 방지

**규칙**

- `.env.example`에는 **빈 값 또는 플레이스홀더만** 넣는다
- 실제 값은 `.env`(gitignore) 또는 배포 플랫폼 시크릿에만 둔다

**커밋 전 확인**

```bash
git diff --cached | grep -inE "secret|password|api[_-]?key|token|postgresql://|sb_secret|eyJ"
```

플레이스홀더나 기본값(`change-me` 등)은 걸려도 무해하다. **실제 값처럼 보이는 문자열**만 확인하면 된다.

**노출됐다면**

즉시 키를 폐기하고 재발급한다. 커밋 이력에서 지우는 것보다 **키를 무효화하는 게 먼저**다.

### 스테이징 검토도 함께

`git add -A` 후에는 무엇이 포함됐는지 확인한다. 이번 작업에서 `.idea/`(IDE 설정)가 추적될 뻔해 `.gitignore`에 추가했다.

---

## 2. 환경변수는 재배포해야 반영된다

Vercel은 환경변수를 **빌드/배포 시점에 주입**한다. 값만 추가하면 **이미 떠 있는 배포에는 적용되지 않는다.**

```bash
vercel env add KEY production
vercel deploy --prod --yes    # ← 반드시 재배포
```

### Expo는 더 엄격하다

`EXPO_PUBLIC_*` 값은 **번들에 구워진다.** 반드시 이 순서를 지킨다.

```
1. vercel env add EXPO_PUBLIC_API_BASE_URL production
2. vercel deploy --prod --yes
```

순서가 바뀌면 이전 값이 그대로 남는다.

### 값 확인

```bash
vercel env ls                                    # 이름만 (값은 Encrypted)
vercel env pull /tmp/check.env --environment=production
```

> `vercel env pull`은 **평문 파일**을 만든다. 확인 후 반드시 삭제한다.

---

## 3. Hobby 플랜에서는 Git 자동 배포가 안 된다

### 증상

`vercel link` 시 다음 오류.

```
The repository "Frontend" is private and owned by an organization,
which is not supported on the Hobby plan.
```

`vercel project inspect`로도 Git 섹션이 비어 있음을 확인했다.

### 영향

**푸시해도 자동 배포되지 않는다.** 현재는 CLI로 수동 배포 중이다.

기존 `.github/workflows/cicd.yml`은 K3s용(Docker → K3s)이고 `deploy` 브랜치에만 걸려 있어 무관하다.

### 선택지

| 방법 | 필요한 것 | 비고 |
| --- | --- | --- |
| GitHub Actions + Vercel CLI | `VERCEL_TOKEN` 시크릿 | 워크플로 작성됨, 토큰 미등록 |
| Pro 업그레이드 | 유료 | 프리뷰 배포까지 자동 |

Actions 방식은 `VERCEL_TOKEN`이 없으면 **푸시마다 실패 알림**이 오므로, 토큰 등록 전에는 워크플로를 커밋하지 않았다.

---

## 4. 프리뷰 배포는 SSO로 막혀 있다

프리뷰 URL에 접근하면 302로 `vercel.com/sso-api`로 리다이렉트된다.

```
location: https://vercel.com/sso-api?url=...
```

Deployment Protection 기본 설정이다. **API를 외부에서 테스트하려면 프로덕션 배포가 필요하다.**

---

## 5. `git fetch origin`이 원격 추적 ref를 갱신하지 않을 수 있다

### 증상

머지된 PR이 `origin/main`에 안 보여 **작업이 유실된 줄 알았다.**

```bash
git fetch origin
git log --oneline HEAD..origin/main   # 비어 있음
```

### 확인

`gh`로 실제 머지 상태를 봤다.

```bash
gh pr view 3 --json number,baseRefName,mergedAt,mergeCommit,state
# → MERGED, base=main, mergeCommit=f5b60f1...
```

머지는 되어 있었다. **로컬 추적 ref만 갱신되지 않은 것이었다.**

### 해결

refspec을 명시해 다시 받는다.

```bash
git fetch origin '+refs/heads/main:refs/remotes/origin/main'
```

### 유실 여부 판단

머지 커밋과 내 커밋의 관계를 확인하면 확실하다.

```bash
git merge-base --is-ancestor <내커밋> <머지커밋> && echo "포함됨" || echo "유실"
```

이번 건은 머지 커밋이 내 커밋을 **첫 부모**로 두고 있어 유실이 없음을 확인했다.

---

## 6. 검증용 데이터는 반드시 정리한다

배포된 API를 실제로 검증하려면 테스트 유저·이미지·대화가 필요했다. 매번 만들고 **매번 지웠다.**

```
chat_messages → chat_conversations → closet_items → images → users
```

FK 때문에 **삭제 순서가 있다.** 자식부터 지운다.

스토리지 객체도 함께 지운다. 단, [참조 카운팅](./03-image-storage.md#3--삭제할-때-참조-카운팅이-필요하다)에 유의한다.

> 프로덕션 DB에서 검증했다. 스테이징이 없는 상황에서는 **정리를 검증 과정의 일부로** 취급한다.

---

## 7. `POST /auth/login`이 비밀번호를 검증하지 않았다

### 증상

없다. 그게 문제다. 아무도 호출하지 않아 조용히 열려 있었다.

### 원인

```python
@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    # MVP 단계에서는 계정 저장소 연결 전까지 입력 이메일을 subject로 JWT만 발급한다.
    return TokenResponse(access_token=create_access_token(payload.email))
```

`LoginRequest`는 `password` 필드를 받는데 **어디서도 검증하지 않는다.**
아무 이메일이나 보내면 그 이메일의 토큰이 나온다. 주석에 "MVP 단계"라고 적혀 있지만
라우터에 등록돼 실제로 열려 있는 엔드포인트였다.

그리고 그 토큰은 **쓸 수도 없었다.** `create_access_token(payload.email)`은 `sub`에
이메일을 넣는데 `deps.get_current_user`는 그 값으로 `User.id`를 조회한다.

```python
select(User).where(User.id == str(user_id))   # user_id = "a@b.com"
```

`User.id`는 UUID 컬럼이라 asyncpg가 `invalid input syntax for type uuid`를 던진다.
**401이 아니라 500이다.** 정상 경로인 소셜 로그인은 `user.id`를 넣으므로 문제없다.

### 해결 — 구현이 아니라 제거

비밀번호를 검증하도록 고치는 선택지는 택하지 않았다. **저장소가 없다.**

- `password_hash`는 소셜 로그인에서 `None`으로만 저장된다
- 해시 라이브러리가 의존성에 없다
- 프론트엔드는 이 엔드포인트를 호출하지 않는다

없는 기능을 새로 만드는 대신 열린 문을 닫았다. `LoginRequest` 스키마도 함께 지웠다 —
남겨 두면 다음 사람이 되살리기 쉽다. 로그인 경로는 `/auth/google`, `/auth/apple` 둘뿐이다.

> "MVP라서 나중에"라고 적은 주석은 **지금 열려 있다는 사실을 바꾸지 못한다.**
> 임시 구현을 남길 거면 라우터에서 빼거나 환경으로 막는다.

---

## 8. 클라이언트가 준 URL을 서버가 그대로 가져왔다 (SSRF)

### 증상

없다. 이것도 조용한 종류다.

### 원인

VLM이 이미지를 분석하려면 바이트를 인라인해야 해서 서버가 직접 받아 온다.
그런데 스킴만 확인하고 있었다.

```python
scheme = urlparse(image_url).scheme.lower()
if scheme not in ALLOWED_IMAGE_SCHEMES:   # http/https만 확인
    raise ValueError(...)
response = await client.get(image_url)     # 그대로 요청
```

이 `image_url`은 `MessageSendRequest.image_urls`와 `RecommendationCreateRequest.image_urls`를
통해 **클라이언트가 임의로 넣는 값**이다. 우리 스토리지에서 온 URL인지 확인하지 않는다.

로그인한 사용자가 이런 주소를 넣으면 서버가 대신 요청한다.

| 주소 | 노리는 것 |
| --- | --- |
| `http://169.254.169.254/latest/meta-data/` | 클라우드 메타데이터·자격증명 |
| `http://localhost:8000/...` | 내부 전용 엔드포인트 |
| 사내망 IP | 외부에서 닿지 않는 서비스 |

`follow_redirects=True`라 리다이렉트도 따라가고, **받은 내용이 Gemini로 넘어가므로
유출 경로까지 열려 있었다.** 크기 검사도 `response.content`를 다 읽은 뒤에 이뤄져
8MB 제한 이전에 이미 메모리에 올라왔다.

### 해결

**호스트 허용 목록.** 설정에서 만들므로 환경마다 다르다.

```python
@staticmethod
def _allowed_image_hosts() -> set[str]:
    hosts = set()
    for candidate in (settings.supabase_url, settings.public_base_url):
        host = urlparse(str(candidate or "")).hostname
        if host:
            hosts.add(host.lower())
    return hosts
```

운영 값으로는 `<ref>.supabase.co`와 `aidfit-backend.vercel.app` 두 곳이고,
스토리지 경로와 업로드 폴백 경로가 모두 덮인다.

**리다이렉트는 막지 않고 홉마다 다시 검사한다.** 첫 주소만 보면 신뢰하는 호스트가
한 번 넘기는 것으로 우회된다. 스토리지가 서명된 주소로 넘기는 정상 동작은 유지해야 하므로
차단이 아니라 재검사다. 3홉을 넘으면 포기한다.

**본문은 스트리밍하며 자른다.** 다 받은 뒤 크기를 재면 상한을 넘는 응답이 이미 올라와 있다.

```python
async for chunk in response.aiter_bytes():
    size += len(chunk)
    if size > limit:
        raise RuntimeError(...)
```

### 기존 데이터 확인

허용 목록은 이미 저장된 사진을 막을 수 있다. 운영 DB로 확인했다 —
`closet_items.image_url` 전량이 허용 호스트(Supabase)였다.

> 허용 목록을 넣을 때는 **이미 저장된 값이 그 목록을 통과하는지** 먼저 센다.
> 통과하지 못하면 기존 사용자의 기능이 조용히 멈춘다.

### 회귀 테스트

차단이 실제로 되는지, 정상 경로가 안 막히는지 둘 다 박았다.

| 테스트 | 확인 |
| --- | --- |
| 낯선 호스트 · 메타데이터 IP · `localhost`/사설망 · `file://` | 거부 |
| 목록 밖으로 리다이렉트 | 거부, **그 목적지로 요청이 나가지 않는 것**까지 단언 |
| 같은 호스트 리다이렉트 | 허용 (서명 주소) |
| 리다이렉트 루프 | 3홉 초과 시 포기 |

---

## 체크리스트

- [ ] `.env.example`에 실제 값이 들어가지 않았는가
- [ ] 커밋 전 스테이징 diff를 시크릿 패턴으로 훑었는가
- [ ] 환경변수 추가 후 재배포했는가
- [ ] `EXPO_PUBLIC_*`는 env 등록 → 배포 순서를 지켰는가
- [ ] `vercel env pull`로 만든 평문 파일을 지웠는가
- [ ] 검증용 데이터를 정리했는가
- [ ] 인증 없이 토큰을 내주는 엔드포인트가 라우터에 등록돼 있지 않은가
- [ ] "MVP라서 나중에"라고 적힌 임시 구현이 실제로 열려 있지 않은가
- [ ] 클라이언트가 준 URL로 서버가 요청을 보내는 곳이 있는가 — 호스트를 검사하는가
- [ ] 리다이렉트를 따라간다면 홉마다 다시 검사하는가
- [ ] 외부 응답을 다 읽은 뒤에 크기를 재고 있지 않은가
- [ ] JWT 시크릿이 기본값(`change-me`)으로 서명되고 있지 않은가

---

### 관련 문서

- [배포·인프라](./01-deployment-infra.md) — Vercel 설정
- [이미지 스토리지](./03-image-storage.md) — 스토리지 삭제 시 주의점
- [12 로컬은 통과하는데 배포만 죽는다](./12-works-locally-fails-deployed.md) — 환경변수·접속 문자열 결함
