from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.db.session import get_db
from app.schemas.auth import AuthResponse, LoginRequest, SocialLoginRequest, TokenResponse
from app.services.auth_service import AuthService
from app.services.oauth_service import OAuthTokenVerifier

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    # MVP 단계에서는 계정 저장소 연결 전까지 입력 이메일을 subject로 JWT만 발급한다.
    return TokenResponse(access_token=create_access_token(payload.email))


@router.post("/google", response_model=AuthResponse)
async def login_with_google(
    payload: SocialLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    identity = await OAuthTokenVerifier().verify_google(payload.id_token, payload.nonce)
    if payload.display_name and not identity.name:
        identity = identity.__class__(**{**identity.__dict__, "name": payload.display_name})
    return await AuthService().login_social(db, identity)


@router.post("/apple", response_model=AuthResponse)
async def login_with_apple(
    payload: SocialLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    identity = await OAuthTokenVerifier().verify_apple(payload.id_token, payload.nonce)
    if payload.display_name and not identity.name:
        identity = identity.__class__(**{**identity.__dict__, "name": payload.display_name})
    return await AuthService().login_social(db, identity)
