# AID-FIT Backend

AID-FIT 백엔드는 React Native 프론트엔드에서 업로드한 의류 이미지와 텍스트 요청을 받아 VLM 분석, RAG 검색, LangGraph 기반 에이전트 추론을 거쳐 코디 추천 결과를 반환하는 FastAPI 서버입니다.

Gemini VLM/LLM과 pgvector 상품 검색을 사용합니다. 목업 경로는 없으므로 `GEMINI_API_KEY`가 필요합니다.

VLM(`app/services/vlm_service.py`)과 LLM은 Gemini에, RAG는 Supabase pgvector에 연결되어 있습니다. `GEMINI_API_KEY`가 있어야 동작합니다.

### VLM 동작 방식

이미지 분석은 호출 경로에 따라 두 가지 모드로 동작합니다.

| 경로 | 메서드 | 모드 |
| --- | --- | --- |
| 추천 요청 (`POST /recommendations`) | `analyze_many()` | **멀티 아이템** — 코디 사진 1장에서 상의/하의/신발 등을 각각 분리해 반환 |
| 옷장 등록 (`POST /images`) | `analyze()` | **단일 아이템** — 사진 1장 = 옷 1벌 |

- 이미지당 API 호출은 1회이며, 멀티 아이템이어도 호출 수는 늘지 않습니다.
- 한 이미지에서 추출할 아이템 개수는 `VLM_MAX_ITEMS_PER_IMAGE`로 제한됩니다.
- 최상위 `is_fashion_item`은 **모든 입력 이미지가 각각 최소 1개의 의류를 포함할 때만** `true`입니다.
- `price`, `product_url`은 업로드 사진에서 알 수 없는 값이므로 항상 `null`로 고정됩니다.

옷장 등록을 멀티 아이템으로 확장하려면 `closet_items.image_id`의 unique 제약 해제와 아이템별 썸네일 크롭이 선행되어야 합니다.

## Tech Stack

| Layer | 선택 |
| --- | --- |
| API | FastAPI |
| DB | PostgreSQL |
| ORM | SQLAlchemy Async |
| Agent | LangGraph |
| Auth | JWT Bearer |
| Image Storage | Local uploads first, S3 교체 가능 |

## Directory Structure

```text
app/
  main.py
  core/
    config.py              # 환경변수, CORS, 업로드 경로
    security.py            # JWT, 비밀번호 해시
  api/v1/
    auth.py                # 로그인
    users.py               # 사용자/선호 스타일
    images.py              # 이미지 업로드
    recommendations.py     # 추천 생성/조회
    products.py            # 상품 조회
    feedback.py            # 클릭/저장 이벤트
    health.py              # 헬스 체크
  db/
    session.py             # Async DB 세션
    models.py              # PostgreSQL 테이블 모델
  schemas/
    *.py                   # Request/Response DTO
  services/
    storage_service.py     # 파일 저장
    vlm_service.py         # Vision 팀 연동 지점
    rag_service.py         # RAG 팀 연동 지점
    llm_service.py         # LLM 출력 생성 지점
    recommendation_service.py
  agent/
    state.py               # AgentState
    prompts.py             # 시스템 프롬프트/검색 쿼리 생성
    nodes.py               # VLM/RAG/LLM 노드
    agent_pipeline.py      # LangGraph 컴파일 및 실행
```

## LLM-based LangGraph Flow

추천 Agent의 분기 결정은 Gemini 구조화 출력으로 수행합니다. LangGraph 노드는 키워드 규칙으로 의도를 결정하지 않고, 각 LLM 결과를 Pydantic 계약으로 검증한 뒤 다음 노드로 라우팅합니다.

```text
입력 검증
  -> LLM 의도 분류
     -> general_chat: 일반 답변 LLM -> 종료
     -> fashion_service
        -> 이미지가 있으면 VLM
        -> LLM 질의 정제(query + 대화 내역 + VLM 결과)
        -> LLM 검색 계획(retrieval target + reuse/retrieve + shown/unseen scope)
           -> reuse: 비교·설명은 노출 후보, "하나 더"는 미노출 후보 재사용
           -> retrieve: 후보 소진·TTL 만료·조건 변경 시 closet / musinsa / hybrid RAG 실행
        -> 후보 랭킹
        -> RAG 후보만 사용하는 최종 답변 LLM
```

채팅 경로는 RAG 후보 풀, 누적 노출 `item_ref`, 원 검색 질의·대상·조회 시각을 assistant 메시지의 비공개 `_agent_context`에 저장합니다. "하나 더", "다른 상품"처럼 같은 의도의 추가 추천은 아직 노출되지 않은 후보만 재랭킹해 RAG 호출을 생략합니다. 비교·설명 요청은 이미 노출된 후보를 사용할 수 있고, 미노출 후보 소진·TTL 만료·주제/카테고리/소스/필수 조건 변경 시에만 새 RAG를 수행합니다. 미노출 후보가 소진된 재검색에는 누적 노출 ID가 제외 필터로 전달됩니다. 이 비공개 컨텍스트는 채팅 조회 API 응답에서 제거됩니다.

라우팅 판단은 모두 Gemini 구조화 출력으로 이뤄집니다. 네트워크 없이 도는 결정적 더블은 `tests/fake_ai.py`에 있으며 테스트에서만 씁니다.

## Local Setup

### Windows PowerShell에서 Agent만 채팅 테스트

프론트엔드, FastAPI 서버, PostgreSQL, Docker를 실행하지 않고 현재 LangGraph Agent를
PowerShell에서 바로 대화형으로 확인할 수 있습니다.

화면형 상세 설명은 [로컬 Agent 채팅 HTML 가이드](docs/local_agent_chat_guide.html)를 확인하세요.

```powershell
# .env의 GEMINI_API_KEY를 사용한다
.\scripts\run_agent_chat.ps1

# .env의 GEMINI_API_KEY를 사용하는 실제 LLM 테스트
.\scripts\run_agent_chat.ps1 -Mode Gemini
```

첫 실행 시 저장소의 `.venv`를 만들고 `requirements.txt`를 자동 설치합니다. 대화 중에는
`/reset`으로 대화 문맥을 지우고, `/trace`로 intent·검색 분기·RAG 재사용 여부를 확인할 수
있습니다. `/image <URL>`을 입력하면 다음 질문에 이미지 URL을 첨부합니다.

한 번만 실행하고 종료하는 smoke test도 지원합니다.

```powershell
.\scripts\run_agent_chat.ps1 -Mode Mock -Query "화이트 니트에 어울리는 바지 추천해줘" -Trace
```

이 실행기는 메모리 안에서만 대화 내역과 직전 RAG 후보를 유지하므로 DB에는 아무것도
저장하지 않습니다.

### 전체 백엔드 로컬 실행

```bash
cd /Users/mac/Desktop/Coding/_AIDFIT_backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d
python scripts/init_db.py
uvicorn app.main:app --reload
```

Swagger 문서는 서버 실행 후 `http://localhost:8000/docs`에서 확인할 수 있습니다.

## K3s Deployment

K3s 배포 파일과 운영 스크립트가 포함되어 있습니다.

```bash
cp .env.k3s.example .env.k3s
chmod +x scripts/k3s/*.sh
scripts/k3s/deploy.sh
```

배포 구조와 GitHub Actions secret 설정은 [docs/ops/k3s_deployment.md](docs/ops/k3s_deployment.md)를 확인하세요.

## Environment

| Variable | Description | Example |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL async 연결 문자열 | `postgresql+asyncpg://aidfit:aidfit@localhost:5432/aidfit` |
| `JWT_SECRET_KEY` | JWT 서명 키 | `change-me` |
| `LOCAL_UPLOAD_DIR` | 로컬 이미지 저장 폴더 | `uploads` |
| `PUBLIC_BASE_URL` | 업로드 URL 생성 기준 | `http://localhost:8000`, `https://api.aidfit.o-r.kr` |
| `CORS_ORIGINS` | 프론트엔드 허용 Origin 목록 | `http://localhost:8081,http://localhost:19006,http://devse.kr:12571` |
| `GEMINI_API_KEY` | Gemini API 키. LLM과 VLM이 함께 사용 | `AIza...` |
| `VLM_MODEL` | 이미지 분석 모델. 비우면 `GEMINI_MODEL` 사용 | `gemini-2.5-flash` |
| `VLM_TIMEOUT_SECONDS` | 이미지 다운로드 및 분석 타임아웃 | `30` |
| `VLM_MAX_CONCURRENCY` | 이미지 동시 분석 개수 | `4` |
| `VLM_MAX_IMAGE_BYTES` | 분석 허용 이미지 최대 크기 | `8388608` |
| `VLM_MAX_ITEMS_PER_IMAGE` | 코디 사진 1장에서 추출할 아이템 최대 개수 | `8` |
| `RAG_CANDIDATE_CACHE_TTL_SECONDS` | 채팅 후보 풀 재사용 TTL. `0` 이하면 만료하지 않음 | `900` |
| `RAG_VECTOR_DB_PATH` | 영속 ChromaDB 디렉터리 | `data/chromadb_final` |
| `RAG_COLLECTION_NAME` | 상품 검색 컬렉션 이름 | `musinsa` |
| `RAG_EMBEDDING_MODEL` | 인덱스 생성에 사용한 임베딩 모델 | `jhgan/ko-sroberta-multitask` |
| `RAG_EMBEDDING_CACHE_PATH` | Hugging Face 임베딩 모델 캐시 디렉터리 | `data/huggingface` |
| `RAG_EMBEDDING_LOCAL_FILES_ONLY` | 로컬 모델 파일만 허용할지 여부 | `false` |
| `GOOGLE_CLIENT_IDS` | 허용할 Google OAuth client ID 목록 | `ios-client-id,web-client-id` |
| `APPLE_CLIENT_IDS` | 허용할 Apple Bundle ID 또는 Services ID 목록 | `com.aidfit.app` |
| `AUTH_ALLOW_UNVERIFIED_TOKENS` | 로컬 테스트용 서명 검증 우회. 운영 금지 | `false` |

### Vector DB

정적 카탈로그 경로(`RAG_VECTOR_BACKEND=static`)를 쓸 때는 다음 구조로 ChromaDB를 배치합니다.

```text
data/
  chromadb_final/
    chroma.sqlite3
    <segment-id>/
      data_level0.bin
      header.bin
      index_metadata.pickle
      length.bin
      link_lists.bin
```

`data/chromadb_final`은 런타임 데이터이므로 Git과 Docker 빌드 컨텍스트에서 제외됩니다. 배포 환경에서는 같은 경로에 별도 볼륨이나 데이터 아티팩트를 마운트해야 합니다. 첫 검색 시 임베딩 모델이 로컬 캐시에 없고 `RAG_EMBEDDING_LOCAL_FILES_ONLY=false`이면 Hugging Face에서 모델을 내려받습니다.

## API Spec

Base URL:

```text
http://localhost:8000/api/v1
```

### Health

#### `GET /health`

서버 상태 확인.

Response:

```json
{
  "status": "ok"
}
```

### Auth

#### `POST /auth/login`

MVP용 로그인 엔드포인트입니다. 현재는 사용자 저장소 검증 없이 입력 이메일을 subject로 JWT를 발급합니다.

Request:

```json
{
  "email": "demo@aid-fit.local",
  "password": "password"
}
```

#### `POST /auth/google`

프론트엔드가 Google SDK에서 받은 `id_token`을 백엔드로 전달하면, 백엔드는 Google JWKS로 서명을 검증하고 `iss`, `aud`, `exp`, 선택적 `nonce`를 확인한 뒤 내부 JWT를 발급합니다.

Request:

```json
{
  "id_token": "google-id-token",
  "nonce": "optional-nonce",
  "display_name": "김태훈"
}
```

Response:

```json
{
  "access_token": "aidfit-jwt",
  "token_type": "bearer",
  "user": {
    "id": "user-uuid",
    "email": "user@gmail.com",
    "nickname": "김태훈",
    "provider": "google"
  }
}
```

Validation rules:

| Claim | Rule |
| --- | --- |
| `sub` | Google 계정의 고유 식별자. DB의 `social_identities.provider_sub`로 저장 |
| `iss` | `https://accounts.google.com` 또는 `accounts.google.com` |
| `aud` | `.env`의 `GOOGLE_CLIENT_IDS` 중 하나 |
| `exp` | 만료 전이어야 함 |
| `nonce` | 요청에 `nonce`가 있으면 토큰 claim과 일치해야 함 |

#### `POST /auth/apple`

프론트엔드가 Sign in with Apple에서 받은 identity token을 백엔드로 전달하면, 백엔드는 Apple JWKS로 서명을 검증하고 `iss`, `aud`, `exp`, 선택적 `nonce`를 확인한 뒤 내부 JWT를 발급합니다.

Request:

```json
{
  "id_token": "apple-identity-token",
  "nonce": "optional-nonce",
  "display_name": "김태훈"
}
```

Response:

```json
{
  "access_token": "aidfit-jwt",
  "token_type": "bearer",
  "user": {
    "id": "user-uuid",
    "email": "private-relay-or-real-email@privaterelay.appleid.com",
    "nickname": "김태훈",
    "provider": "apple"
  }
}
```

Validation rules:

| Claim | Rule |
| --- | --- |
| `sub` | Apple 계정의 앱 기준 고유 식별자. DB의 `social_identities.provider_sub`로 저장 |
| `iss` | `https://appleid.apple.com` |
| `aud` | `.env`의 `APPLE_CLIENT_IDS` 중 하나 |
| `exp` | 만료 전이어야 함 |
| `nonce` | 요청에 `nonce`가 있으면 토큰 claim과 일치해야 함 |

주의: Apple은 최초 로그인 이후 이름을 다시 내려주지 않을 수 있으므로, 프론트엔드는 최초 응답의 이름을 `display_name`으로 함께 보내고 백엔드는 즉시 저장해야 합니다.

Response:

```json
{
  "access_token": "jwt-token",
  "token_type": "bearer"
}
```

### Users

#### `GET /users/me`

현재 사용자 프로필과 선호 스타일을 조회합니다.

Response:

```json
{
  "id": "user_demo",
  "email": "demo@aid-fit.local",
  "nickname": "AID-FIT 사용자",
  "role": "user",
  "age_range": "20대",
  "gender": "men",
  "height_cm": 178,
  "styles": ["캐주얼", "미니멀"],
  "preferred_colors": [],
  "avoid_items": [],
  "sizes": {}
}
```

#### `PATCH /users/me/preferences`

사용자 선호 스타일을 저장합니다.

Request:

```json
{
  "age_range": "20대",
  "gender": "남성",
  "height_cm": 178,
  "styles": ["캐주얼", "스트릿"],
  "preferred_colors": ["화이트", "네이비"],
  "avoid_items": ["스키니진"],
  "sizes": {
    "top": "L",
    "bottom": "M"
  }
}
```

`gender`는 `남성 / 여성 / male / women's / 공용` 등을 받아 카탈로그가 쓰는 `men | women | unisex`로 정규화해 저장합니다. 알 수 없는 값은 422입니다. `height_cm`은 100~250 사이의 정수입니다.

`styles`, `preferred_colors`, `avoid_items`, `sizes`는 **전체 교체**입니다. 반면 `gender`와 `height_cm`은 **본문에 담긴 경우에만** 반영되므로, 두 필드를 모르는 클라이언트가 프로필을 저장해도 값이 지워지지 않습니다.

Response: `GET /users/me`와 동일한 구조. `gender`는 정규화된 값(`men`)으로 돌아옵니다.

### Chats

추천 탭의 대화입니다. 모든 경로가 액세스 토큰의 사용자로 범위를 좁히며, 남의 대화는 존재 여부를 알리지 않고 404로 답합니다.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/chats` | 대화 생성 (201) |
| `GET` | `/chats` | 대화 목록 |
| `GET` | `/chats/{conversation_id}/messages` | 메시지 페이지 (`limit`, `cursor`) |
| `POST` | `/chats/{conversation_id}/messages` | 질문 전송 |
| `DELETE` | `/chats/{conversation_id}` | 대화 1건 삭제 (204) |
| `DELETE` | `/chats` | 전체 대화 삭제 (204, 멱등) |

#### `POST /chats/{conversation_id}/messages`

Request:

```json
{
  "query": "이 재킷에 어울리는 바지 추천해줘",
  "image_urls": [],
  "closet_item_ids": ["closet_001", "closet_002"]
}
```

`closet_item_ids`를 비우면 지금까지처럼 옷장 **전체**를 참고합니다. 채우면 그 옷만 이번 질문의 범위가 됩니다(최대 8개). 자기 옷장에 없는 id는 404 `Some closet items were not found`입니다.

`role`과 `user_id`는 서버가 정하며 요청 본문에 담을 수 없습니다(422).

### Images

#### `POST /images`

의류 이미지를 업로드하고 추천 요청에서 사용할 `image_url`을 반환합니다.

Content-Type:

```text
multipart/form-data
```

Form fields:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `file` | file | yes | 업로드할 의류 이미지 |

Response:

```json
{
  "id": "image-id",
  "image_url": "http://localhost:8000/uploads/image-id.jpg",
  "content_type": "image/jpeg"
}
```

### Recommendations

#### `POST /recommendations`

이미지 URL과 사용자 텍스트를 기반으로 코디 추천을 생성합니다.

Request:

```json
{
  "prompt": "주말 데이트에 입을 깔끔한 코디를 추천해줘",
  "image_url": "http://localhost:8000/uploads/sample.jpg",
  "user_id": "user_demo",
  "context": {
    "weather": "warm",
    "occasion": "date"
  }
}
```

Response:

```json
{
  "id": "rec_abc123",
  "title": "가볍고 단정한 데이트룩",
  "summary": "VLM이 추출한 밝은 컬러, 여유로운 핏, 캐주얼한 무드를 기준으로 검색된 상품 안에서만 조합했습니다.",
  "tags": ["캐주얼", "단정함", "데일리"],
  "items": [
    {
      "id": "prod_shirt_001",
      "category": "상의",
      "name": "린넨 오버 셔츠",
      "reason": "이미지에서 보이는 밝고 단정한 무드와 잘 이어지는 상의입니다.",
      "imageTone": "#f5f7fa",
      "product": {
        "id": "prod_shirt_001",
        "brand": "AID BASIC",
        "price": 39900,
        "imageUrl": null
      }
    }
  ],
  "vlm_result": {
    "is_clothing": true,
    "confidence": 92,
    "colors": ["화이트", "라이트 블루"],
    "materials": ["코튼", "린넨"],
    "fit": ["오버핏", "와이드"],
    "mood": ["캐주얼", "단정함"],
    "detected_item": "셔츠"
  }
}
```

Fallback response when the image is not recognized as clothing:

```json
{
  "id": "rec_abc123",
  "title": "의류 사진을 다시 올려주세요",
  "summary": "업로드된 이미지에서 의류를 안정적으로 확인하지 못했습니다.",
  "tags": ["이미지 확인 필요"],
  "items": [],
  "vlm_result": {
    "is_clothing": false,
    "confidence": 35
  }
}
```

#### `GET /recommendations/{recommendation_id}`

추천 결과 상세 조회.

### Products

#### `GET /products/{product_id}`

상품 상세 조회.

Response:

```json
{
  "id": "prod_shirt_001",
  "brand": "AID BASIC",
  "name": "린넨 오버 셔츠",
  "category": "상의",
  "price": 39900,
  "image_url": null,
  "tags": ["린넨", "오버핏", "화이트"]
}
```

### Feedback

#### `POST /feedback/events`

추천 클릭, 저장, 싫어요 같은 사용자 반응을 저장합니다. 이 로그는 추후 추천 품질 개선과 LoRA 파인튜닝 데이터셋의 기반이 됩니다.

Request:

```json
{
  "user_id": "user_demo",
  "recommendation_id": "rec_abc123",
  "product_id": "prod_shirt_001",
  "event_type": "product_click",
  "metadata": {
    "screen": "RecommendationResult"
  }
}
```

Response:

```json
{
  "id": "evt_abc123",
  "event_type": "product_click"
}
```

## PostgreSQL Schema Plan

| Table | Purpose |
| --- | --- |
| `users` | 사용자 계정 |
| `social_identities` | Google/Apple provider와 provider `sub` 매핑 |
| `user_preferences` | 선호 스타일, 색상, 회피 아이템, 사이즈, 성별(`men\|women\|unisex`), 키(cm) |
| `chat_conversations` | 추천 탭 대화. 삭제 시 메시지도 함께 지웁니다 |
| `chat_messages` | 대화 메시지. `payload`에 첨부 이미지와 옷장 선택 스냅샷이 담깁니다 |
| `images` | 업로드 이미지 메타데이터 |
| `products` | 의류 상품 원본 메타데이터 |
| `product_embeddings` | RAG 검색용 임베딩 메타데이터 |
| `recommendation_requests` | 추천 요청 단위 로그 |
| `vlm_analyses` | VLM 분석 결과 JSON |
| `recommendations` | 최종 추천 제목/요약/태그 |
| `recommendation_items` | 추천 결과와 상품 매핑 |
| `feedback_events` | 클릭, 저장, 싫어요 등 사용자 반응 |

## Team Integration Contracts

### Backend -> Agent Contract

`POST /api/v1/recommendations`는 Agent 호출 계약을 그대로 수용합니다. 기존 프론트 호환을 위해 `prompt`도 임시로 `query` alias로 허용합니다.

```json
{
  "user_id": "user_001",
  "query": "이 셔츠랑 어울리는 바지를 추천해줘",
  "image_url": "https://example.com/input-shirt.jpg",
  "closet_item_id": null,
  "recommendation_target": "musinsa",
  "context": {
    "season": "spring",
    "occasion": "daily",
    "preferred_style": ["minimal", "casual"]
  }
}
```

### Agent -> Backend Contract

```json
{
  "status": "success",
  "message": "화이트 셔츠에는 미니멀한 세미 와이드 데님 팬츠가 잘 어울립니다.",
  "recommendations": [
    {
      "item_id": "musinsa_10001",
      "source": "musinsa",
      "item_name": "세미 와이드 데님 팬츠",
      "brand": "Example Brand",
      "category": "pants",
      "image_url": "https://image.musinsa.com/10001.jpg",
      "product_url": "https://www.musinsa.com/products/10001",
      "price": 59000,
      "reason": "화이트 셔츠의 깔끔한 무드와 데님의 캐주얼함이 잘 어울립니다."
    }
  ],
  "style_guide": {
    "summary": "미니멀 캐주얼 코디",
    "tips": [
      "상의가 밝은 색이므로 하의는 중청 또는 진청 계열이 안정적입니다.",
      "신발은 화이트 스니커즈를 추천합니다."
    ]
  }
}
```

### Vision/VLM Contract

`VlmService.analyze(image_url, query)`는 옷장 이미지 분석 결과를 다음 구조로 반환해야 합니다. `"sense of season"`은 API 출력에서도 같은 키를 유지합니다.

```json
{
  "name": "에이프 헤드 클리어 백(M) BLACK",
  "brand": "베이프",
  "price": 115000,
  "category": "가방",
  "sub_category": "남자 가방",
  "gender": "men",
  "image_url": "https://image.msscdn.net/images/goods_img/20260325/6190928/6190928_17751824974403_500.jpg",
  "product_url": "https://www.musinsa.com/products/6190928",
  "color": "black",
  "material": "pvc",
  "fit": "none",
  "pattern": "graphic",
  "mood": "street",
  "sense of season": "summer",
  "is_match": true
}
```

`is_match=false`이면 LangGraph는 RAG 검색으로 넘어가지 않고 fallback 응답을 반환합니다.

### Agent/LLM Contract

LLM은 반드시 RAG가 반환한 `rag_items` 내부 상품만 추천해야 합니다. 없는 상품명, 브랜드, 가격, URL은 생성하지 않습니다.

## Current Limitations

- Alembic migration 파일은 아직 생성하지 않았습니다.
- Auth는 JWT 발급 골격만 있으며 실제 사용자 검증은 미구현입니다.
- S3 저장은 `StorageService` 교체 지점만 마련되어 있고 현재는 로컬 저장입니다.
- 프론트엔드 실연동 시 `src/services/apiClient.ts`의 `baseURL`을 `http://localhost:8000/api/v1`로 변경해야 합니다.
