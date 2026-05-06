from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.db.models import SocialIdentity, User
from app.schemas.auth import AuthResponse, AuthUserResponse
from app.services.oauth_service import VerifiedIdentity


class AuthService:
    async def login_social(self, db: AsyncSession, identity: VerifiedIdentity) -> AuthResponse:
        user = await self._find_user_by_social_identity(db, identity)
        if user is None:
            user = await self._find_or_create_user(db, identity)
            db.add(
                SocialIdentity(
                    user_id=user.id,
                    provider=identity.provider,
                    provider_sub=identity.provider_sub,
                    email=identity.email,
                    raw_claims=identity.claims,
                )
            )
        else:
            await self._update_social_identity(db, identity)

        await db.commit()
        await db.refresh(user)
        return AuthResponse(
            access_token=create_access_token(user.id),
            user=AuthUserResponse(
                id=user.id,
                email=user.email,
                nickname=user.nickname,
                provider=identity.provider,
            ),
        )

    async def _find_user_by_social_identity(
        self, db: AsyncSession, identity: VerifiedIdentity
    ) -> User | None:
        result = await db.execute(
            select(User)
            .join(SocialIdentity)
            .where(
                SocialIdentity.provider == identity.provider,
                SocialIdentity.provider_sub == identity.provider_sub,
            )
        )
        return result.scalar_one_or_none()

    async def _find_or_create_user(self, db: AsyncSession, identity: VerifiedIdentity) -> User:
        user = None
        if identity.email:
            result = await db.execute(select(User).where(User.email == identity.email))
            user = result.scalar_one_or_none()

        if user is not None:
            return user

        nickname = identity.name or self._nickname_from_email(identity.email) or f"{identity.provider} 사용자"
        user = User(email=identity.email, password_hash=None, nickname=nickname)
        db.add(user)
        await db.flush()
        return user

    async def _update_social_identity(self, db: AsyncSession, identity: VerifiedIdentity) -> None:
        result = await db.execute(
            select(SocialIdentity).where(
                SocialIdentity.provider == identity.provider,
                SocialIdentity.provider_sub == identity.provider_sub,
            )
        )
        social_identity = result.scalar_one_or_none()
        if social_identity is None:
            return
        social_identity.email = identity.email
        social_identity.raw_claims = identity.claims

    def _nickname_from_email(self, email: str | None) -> str | None:
        if not email:
            return None
        return email.split("@", 1)[0]
