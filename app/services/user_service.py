from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserPreference
from app.schemas.user import UserPreferenceUpdate


def to_agent_profile(preference: UserPreference | None) -> dict:
    """Convert persisted preferences into the profile contract used by the agent."""
    sizes = preference.sizes if preference and isinstance(preference.sizes, dict) else {}
    return {
        "age_group": sizes.get("age_range"),
        "preferred_styles": list(preference.styles or []) if preference else [],
        # gender는 카탈로그 후보를 실제로 걸러낸다(pgvector 조건절).
        # height_cm은 걸 수 있는 상품 데이터가 없어 LLM의 기장·핏 설명에만 쓰인다.
        "gender": preference.gender if preference else None,
        "height_cm": preference.height_cm if preference else None,
    }


class UserService:
    async def get_preference(self, db: AsyncSession, user: User) -> UserPreference | None:
        return await self.get_preference_for_user_id(db, user.id)

    async def get_preference_for_user_id(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> UserPreference | None:
        result = await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
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
                gender=payload.gender,
                height_cm=payload.height_cm,
            )
            db.add(preference)
        else:
            preference.styles = payload.styles
            preference.preferred_colors = payload.preferred_colors
            preference.avoid_items = payload.avoid_items
            preference.sizes = sizes
            # 이 PATCH는 나머지 필드를 통째로 교체한다. 성별과 키에 같은 규칙을
            # 적용하면, 두 필드를 모르는 구버전 앱이 프로필을 저장할 때마다 값이
            # 지워진다. 그래서 본문에 실제로 들어온 경우에만 반영한다.
            if "gender" in payload.model_fields_set:
                preference.gender = payload.gender
            if "height_cm" in payload.model_fields_set:
                preference.height_cm = payload.height_cm

        await db.flush()
        return preference
