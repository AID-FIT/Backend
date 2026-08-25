from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.auth import AuthResponse, SocialLoginRequest
from app.services.auth_service import AuthService
from app.services.oauth_service import OAuthTokenVerifier

# 이메일·비밀번호 로그인(`POST /auth/login`)은 제거했다. 비밀번호를 검증하지
# 않고 입력한 이메일로 토큰을 발급하고 있었다 — 아무 이메일이나 보내면 그
# 계정의 토큰이 나왔다. 게다가 그 토큰은 sub에 이메일이 들어가는데
# `deps.get_current_user`는 그 값으로 User.id(UUID)를 조회하므로 쓸 수도 없었다.
# 비밀번호 저장소도 없다(password_hash는 소셜 로그인에서 None으로만 저장된다).
# 로그인 경로는 아래 소셜 로그인 둘뿐이다.
router = APIRouter()


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
