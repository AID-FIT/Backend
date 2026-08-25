from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import ImageAsset, User, UserPreference
from app.db.session import get_db
from app.schemas.user import OnboardingCompleteRequest, UserPreferenceUpdate, UserProfileResponse
from app.services.closet_service import ClosetService
from app.services.user_service import UserService

router = APIRouter()


def _profile_response(user: User, preference: UserPreference | None) -> UserProfileResponse:
    sizes = preference.sizes if preference else {}
    return UserProfileResponse(
        id=user.id,
        email=user.email,
        nickname=user.nickname,
        role=user.role,
        age_range=sizes.get("age_range") if isinstance(sizes, dict) else None,
        gender=preference.gender if preference else None,
        height_cm=preference.height_cm if preference else None,
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
    preference = await UserService().upsert_preference(db, current_user, payload)

    await db.commit()
    await db.refresh(preference)
    return _profile_response(current_user, preference)


@router.post("/me/onboarding/complete", response_model=UserProfileResponse)
async def complete_onboarding(
    payload: OnboardingCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    preference = await UserService().upsert_preference(db, current_user, payload)

    if payload.closet_image_ids:
        result = await db.execute(
            select(ImageAsset).where(
                ImageAsset.user_id == current_user.id,
                ImageAsset.id.in_(payload.closet_image_ids),
            )
        )
        images = list(result.scalars().all())
        if len(images) != len(set(payload.closet_image_ids)):
            raise HTTPException(status_code=404, detail="Some closet images were not found")

        closet_service = ClosetService()
        for image in images:
            await closet_service.analyze_and_store(db, current_user, image)

    current_user.role = "user"
    await db.commit()
    await db.refresh(current_user)
    await db.refresh(preference)
    return _profile_response(current_user, preference)
