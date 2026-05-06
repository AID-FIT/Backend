from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import User, UserPreference
from app.db.session import get_db
from app.schemas.user import UserPreferenceUpdate, UserProfileResponse

router = APIRouter()


def _profile_response(user: User, preference: UserPreference | None) -> UserProfileResponse:
    sizes = preference.sizes if preference else {}
    return UserProfileResponse(
        id=user.id,
        email=user.email,
        nickname=user.nickname,
        age_range=sizes.get("age_range") if isinstance(sizes, dict) else None,
        styles=preference.styles if preference else [],
        preferred_colors=preference.preferred_colors if preference else [],
        avoid_items=preference.avoid_items if preference else [],
        sizes=sizes,
    )


@router.get("/me", response_model=UserProfileResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    result = await db.execute(select(UserPreference).where(UserPreference.user_id == current_user.id))
    return _profile_response(current_user, result.scalar_one_or_none())


@router.patch("/me/preferences", response_model=UserProfileResponse)
async def update_preferences(
    payload: UserPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    result = await db.execute(select(UserPreference).where(UserPreference.user_id == current_user.id))
    preference = result.scalar_one_or_none()
    sizes = {**payload.sizes}
    if payload.age_range:
        sizes["age_range"] = payload.age_range

    if preference is None:
        preference = UserPreference(
            user_id=current_user.id,
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

    await db.commit()
    await db.refresh(preference)
    return _profile_response(current_user, preference)
