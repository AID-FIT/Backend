from fastapi import APIRouter

from app.core.security import create_access_token
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    # MVP 단계에서는 계정 저장소 연결 전까지 입력 이메일을 subject로 JWT만 발급한다.
    return TokenResponse(access_token=create_access_token(payload.email))

