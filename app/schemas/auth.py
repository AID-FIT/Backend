from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SocialLoginRequest(BaseModel):
    id_token: str = Field(min_length=10)
    nonce: str | None = None
    display_name: str | None = None


class AuthUserResponse(BaseModel):
    id: str
    email: str | None = None
    nickname: str
    provider: str
    role: str


class AuthResponse(TokenResponse):
    user: AuthUserResponse
