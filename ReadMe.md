# AID-FIT Backend

AID-FIT 백엔드는 React Native 프론트엔드에서 업로드한 의류 이미지와 텍스트 요청을 받아 VLM 분석, RAG 검색, LangGraph 기반 에이전트 추론을 거쳐 코디 추천 결과를 반환하는 FastAPI 서버입니다.

현재 구현은 팀별 Vision/RAG/LLM 모듈이 붙기 전에도 프론트엔드 연동을 진행할 수 있도록 mock 서비스를 기본값으로 제공합니다. 이후 `app/services/vlm_service.py`, `app/services/rag_service.py`, `app/services/llm_service.py` 내부만 실제 구현으로 교체하면 됩니다.

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

## Local Setup

```bash
cd /Users/mac/Desktop/Coding/_AIDFIT_backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d
uvicorn app.main:app --reload
```

Swagger 문서는 서버 실행 후 `http://localhost:8000/docs`에서 확인할 수 있습니다.

## Environment

| Variable | Description | Example |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL async 연결 문자열 | `postgresql+asyncpg://aidfit:aidfit@localhost:5432/aidfit` |
| `JWT_SECRET_KEY` | JWT 서명 키 | `change-me` |
| `LOCAL_UPLOAD_DIR` | 로컬 이미지 저장 폴더 | `uploads` |
| `PUBLIC_BASE_URL` | 업로드 URL 생성 기준 | `http://localhost:8000` |
| `CORS_ORIGINS` | 프론트엔드 허용 Origin 목록 | `http://localhost:8081,http://localhost:19006` |
| `USE_MOCK_AI` | mock AI 사용 여부 | `true` |

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
  "styles": ["캐주얼", "스트릿"],
  "preferred_colors": ["화이트", "네이비"],
  "avoid_items": ["스키니진"],
  "sizes": {
    "top": "L",
    "bottom": "M"
  }
}
```

Response: `GET /users/me`와 동일한 구조.

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

추천 결과 상세 조회. 현재는 DB persist 전 단계이므로 mock 추천 응답을 반환합니다.

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
| `user_preferences` | 선호 스타일, 색상, 회피 아이템, 사이즈 |
| `images` | 업로드 이미지 메타데이터 |
| `products` | 의류 상품 원본 메타데이터 |
| `product_embeddings` | RAG 검색용 임베딩 메타데이터 |
| `recommendation_requests` | 추천 요청 단위 로그 |
| `vlm_analyses` | VLM 분석 결과 JSON |
| `recommendations` | 최종 추천 제목/요약/태그 |
| `recommendation_items` | 추천 결과와 상품 매핑 |
| `feedback_events` | 클릭, 저장, 싫어요 등 사용자 반응 |

## Team Integration Contracts

### Vision/VLM Contract

`VlmService.analyze(image_url, prompt)`는 다음 구조를 반환해야 합니다.

```json
{
  "is_clothing": true,
  "confidence": 92,
  "colors": ["화이트"],
  "materials": ["코튼"],
  "fit": ["오버핏"],
  "mood": ["캐주얼"],
  "detected_item": "셔츠"
}
```

`is_clothing=false`이면 LangGraph는 RAG 검색으로 넘어가지 않고 fallback 응답을 반환합니다.

### RAG Contract

`RagService.search(query, limit)`는 상품 딕셔너리 리스트를 반환해야 합니다.

```json
[
  {
    "id": "prod_1",
    "brand": "AID BASIC",
    "name": "린넨 오버 셔츠",
    "category": "상의",
    "price": 39900,
    "image_url": "https://...",
    "imageTone": "#f5f7fa",
    "tags": ["린넨", "오버핏"]
  }
]
```

### Agent/LLM Contract

LLM은 반드시 RAG가 반환한 `rag_items` 내부 상품만 추천해야 합니다. 없는 상품명, 브랜드, 가격, URL은 생성하지 않습니다.

## Current Limitations

- DB 모델은 작성되어 있으나 API는 아직 mock 응답 중심입니다.
- Alembic migration 파일은 아직 생성하지 않았습니다.
- Auth는 JWT 발급 골격만 있으며 실제 사용자 검증은 미구현입니다.
- S3 저장은 `StorageService` 교체 지점만 마련되어 있고 현재는 로컬 저장입니다.
- 프론트엔드 실연동 시 `src/services/apiClient.ts`의 `baseURL`을 `http://localhost:8000/api/v1`로 변경해야 합니다.

