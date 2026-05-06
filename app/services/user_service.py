from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserPreference
from app.schemas.user import UserPreferenceUpdate


class UserService:
    async def get_preference(self, db: AsyncSession, user: User) -> UserPreference | None:
        result = await db.execute(select(UserPreference).where(UserPreference.user_id == user.id))
        return result.scalar_one_or_none()

    async def upsert_preference(
        self,
        db: AsyncSession,
        user: User,
        payload: UserPreferenceUpdate,
    ) -> UserPreference:
        preference = await self.get_preference(db, user)
        sizes = {**payload.sizes}
        if payload.age_range:
            sizes["age_range"] = payload.age_range

        if preference is None:
            preference = UserPreference(
                user_id=user.id,
                styles=payload.styles,
                preferred_colors=payload.preferred_colors,
                avoid_items=payload.avoid_items,
                sizes=sizes,
            )
            db.add(preference)
        else:
            preference.styles = payload.styles
            preference.preferred_colors = payload.preferred_colors
            preference.avoid_items = payload.avoid_items
            preference.sizes = sizes

        await db.flush()
        return preference
