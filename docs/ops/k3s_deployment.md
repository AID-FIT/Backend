# K3s Deployment Guide

이 배포 구조는 `https://github.com/TEAM-PEENOO/Back-end`의 K3s 패턴을 AID-FIT 백엔드에 맞게 적용한 것입니다.

## Structure

```text
k8s/
  namespace.yaml
  backend-deployment.yaml
  backend-service.yaml
  backend-uploads-pvc.yaml
  backend-rag-pvc.yaml
  postgres-pvc.yaml
  postgres-deployment.yaml
  postgres-service.yaml
scripts/k3s/
  create-secret.sh
  deploy.sh
  seed-rag-data.sh
  status.sh
  logs.sh
  restart.sh
  delete.sh
.github/workflows/cicd.yml
Dockerfile
```

## Runtime Shape

| Component | Name | Namespace | Notes |
| --- | --- | --- | --- |
| Backend Deployment | `aidfit-backend` | `aidfit` | FastAPI on container port `8000` |
| Backend Service | `aidfit-backend-svc` | `aidfit` | K3s `LoadBalancer`, external port `12570` |
| PostgreSQL Deployment | `postgres` | `aidfit` | `postgres:16` |
| PostgreSQL Service | `postgres-svc` | `aidfit` | ClusterIP, port `5432` |
| PVC | `postgres-pvc` | `aidfit` | K3s `local-path`, `5Gi` |
| PVC | `backend-uploads-pvc` | `aidfit` | 업로드 이미지, `2Gi` |
| PVC | `backend-rag-pvc` | `aidfit` | ChromaDB와 임베딩 모델 캐시, `2Gi` |
| Secret | `aidfit-backend-secret` | `aidfit` | App and DB env vars |

## First Manual Deploy on Server

서버에 이미 K3s와 `kubectl`이 설정되어 있다고 가정합니다.

```bash
cd /path/to/_AIDFIT_backend
cp .env.example .env.k3s
vi .env.k3s
chmod +x scripts/k3s/*.sh
scripts/k3s/deploy.sh
scripts/k3s/seed-rag-data.sh
```

상태 확인:

```bash
scripts/k3s/status.sh
scripts/k3s/logs.sh
```

백엔드 URL:

```text
https://api.aidfit.o-r.kr/api/v1/health
https://api.aidfit.o-r.kr/docs
```

## Required `.env.k3s`

```env
POSTGRES_USER=aidfit
POSTGRES_PASSWORD=change-me
POSTGRES_DB=aidfit
JWT_SECRET_KEY=change-me-to-a-long-random-secret
PUBLIC_BASE_URL=https://api.aidfit.o-r.kr
CORS_ORIGINS=http://localhost:8081,http://localhost:19006,http://devse.kr:12571
GOOGLE_CLIENT_IDS=
APPLE_CLIENT_IDS=
AUTH_ALLOW_UNVERIFIED_TOKENS=false
RAG_VECTOR_DB_PATH=/app/data/chromadb_final
RAG_COLLECTION_NAME=musinsa
RAG_EMBEDDING_MODEL=jhgan/ko-sroberta-multitask
RAG_EMBEDDING_CACHE_PATH=/app/data/huggingface
RAG_EMBEDDING_LOCAL_FILES_ONLY=false
```

`DATABASE_URL`은 생략하면 `postgres-svc`를 기준으로 자동 생성됩니다.

## Seed Vector DB

Vector DB와 로컬 모델 캐시는 Docker 이미지 및 Git에 포함되지 않습니다. 최초 배포 후 DB를 RAG PVC에 복사합니다.

```bash
scripts/k3s/seed-rag-data.sh
```

이 스크립트는 로컬 `data/chromadb_final`을 필수로 복사하고, `data/huggingface`가 있으면 모델 캐시도 함께 복사한 뒤 컬렉션 이름·차원·임베딩 개수를 검증합니다. PVC에 모델까지 복사했다면 `RAG_EMBEDDING_LOCAL_FILES_ONLY=true`로 설정할 수 있습니다. PVC가 유지되는 동안 재배포마다 다시 실행할 필요는 없습니다.

## GitHub Actions Deploy

PEENOO repo와 동일하게 `deploy` 브랜치 push 또는 수동 실행으로 배포합니다.

Required GitHub Secrets:

| Secret | Description |
| --- | --- |
| `KUBECONFIG` | 서버 kubeconfig를 base64 인코딩한 값 |
| `K3S_API_SERVER` | GitHub Actions에서 접근 가능한 K3s API server URL |
| `POSTGRES_USER` | PostgreSQL 사용자 |
| `POSTGRES_PASSWORD` | PostgreSQL 비밀번호 |
| `POSTGRES_DB` | PostgreSQL DB 이름 |
| `DATABASE_URL` | 선택. 비우면 workflow가 `postgres-svc` 기준으로 생성 |
| `JWT_SECRET_KEY` | AID-FIT JWT 서명 키 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 예: `10080` |
| `PUBLIC_BASE_URL` | 예: `https://api.aidfit.o-r.kr` |
| `CORS_ORIGINS` | 프론트엔드 origin 목록 |
| `GOOGLE_CLIENT_IDS` | Google OAuth client IDs |
| `APPLE_CLIENT_IDS` | Apple bundle/service IDs |
| `AUTH_ALLOW_UNVERIFIED_TOKENS` | 운영은 `false` |

Kubeconfig base64 생성 예시:

```bash
sudo cat /etc/rancher/k3s/k3s.yaml | base64 | tr -d '\n'
```

## Image

GitHub Actions는 다음 이미지로 빌드/푸시합니다.

```text
ghcr.io/aid-fit/backend:<short-sha>
ghcr.io/aid-fit/backend:latest
```

K3s deployment의 image tag는 workflow에서 short SHA로 치환됩니다.

## Notes

- 이 프로젝트에는 아직 Alembic migration이 없으므로 backend initContainer는 `python scripts/init_db.py`를 실행합니다.
- 나중에 Alembic을 도입하면 `k8s/backend-deployment.yaml`의 initContainer command를 `alembic upgrade head`로 교체하면 됩니다.
- 정적 카탈로그 경로를 쓸 때는 `backend-rag-pvc`를 먼저 시드해야 합니다.
- Sentence Transformer와 Chroma 인덱스를 함께 로드하므로 backend는 메모리 요청 `1Gi`, 제한 `2Gi`로 구성되어 있습니다.
- PostgreSQL 및 backend PVC 삭제는 데이터 삭제입니다. `scripts/k3s/delete.sh`는 PVC와 namespace를 지우지 않습니다.
