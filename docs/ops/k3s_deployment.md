# K3s Deployment Guide

이 배포 구조는 `https://github.com/TEAM-PEENOO/Back-end`의 K3s 패턴을 AID-FIT 백엔드에 맞게 적용한 것입니다.

## Structure

```text
k8s/
  namespace.yaml
  backend-deployment.yaml
  backend-service.yaml
  postgres-pvc.yaml
  postgres-deployment.yaml
  postgres-service.yaml
scripts/k3s/
  create-secret.sh
  deploy.sh
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
| Secret | `aidfit-backend-secret` | `aidfit` | App and DB env vars |

## First Manual Deploy on Server

서버에 이미 K3s와 `kubectl`이 설정되어 있다고 가정합니다.

```bash
cd /path/to/_AIDFIT_backend
cp .env.k3s.example .env.k3s
vi .env.k3s
chmod +x scripts/k3s/*.sh
scripts/k3s/deploy.sh
```

상태 확인:

```bash
scripts/k3s/status.sh
scripts/k3s/logs.sh
```

백엔드 URL:

```text
http://SERVER_IP_OR_DOMAIN:12570/api/v1/health
http://SERVER_IP_OR_DOMAIN:12570/docs
```

## Required `.env.k3s`

```env
POSTGRES_USER=aidfit
POSTGRES_PASSWORD=change-me
POSTGRES_DB=aidfit
JWT_SECRET_KEY=change-me-to-a-long-random-secret
PUBLIC_BASE_URL=http://SERVER_IP_OR_DOMAIN:12570
CORS_ORIGINS=http://localhost:8081,http://localhost:19006,http://FRONTEND_ORIGIN
GOOGLE_CLIENT_IDS=
APPLE_CLIENT_IDS=
AUTH_ALLOW_UNVERIFIED_TOKENS=false
USE_MOCK_AI=true
```

`DATABASE_URL`은 생략하면 `postgres-svc`를 기준으로 자동 생성됩니다.

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
| `PUBLIC_BASE_URL` | 예: `http://SERVER_IP_OR_DOMAIN:12570` |
| `CORS_ORIGINS` | 프론트엔드 origin 목록 |
| `USE_MOCK_AI` | MVP는 `true` |
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
- PostgreSQL PVC 삭제는 데이터 삭제입니다. `scripts/k3s/delete.sh`는 PVC와 namespace를 지우지 않습니다.

