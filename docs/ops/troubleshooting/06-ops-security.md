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

## 체크리스트

- [ ] `.env.example`에 실제 값이 들어가지 않았는가
- [ ] 커밋 전 스테이징 diff를 시크릿 패턴으로 훑었는가
- [ ] 환경변수 추가 후 재배포했는가
- [ ] `EXPO_PUBLIC_*`는 env 등록 → 배포 순서를 지켰는가
- [ ] `vercel env pull`로 만든 평문 파일을 지웠는가
- [ ] 검증용 데이터를 정리했는가

---

### 관련 문서

- [배포·인프라](./01-deployment-infra.md) — Vercel 설정
- [이미지 스토리지](./03-image-storage.md) — 스토리지 삭제 시 주의점
