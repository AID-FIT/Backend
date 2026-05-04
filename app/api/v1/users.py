from fastapi import APIRouter

from app.schemas.user import UserPreferenceUpdate, UserProfileResponse

router = APIRouter()


@router.get("/me", response_model=UserProfileResponse)
async def get_me() -> UserProfileResponse:
    return UserProfileResponse(
        id="user_demo",
        email="demo@aid-fit.local",
        nickname="AID-FIT 사용자",
        styles=["캐주얼", "미니멀"],
    )


@router.patch("/me/preferences", response_model=UserProfileResponse)
async def update_preferences(payload: UserPreferenceUpdate) -> UserProfileResponse:
    return UserProfileResponse(
        id="user_demo",
        email="demo@aid-fit.local",
        nickname="AID-FIT 사용자",
        styles=payload.styles,
        preferred_colors=payload.preferred_colors,
        avoid_items=payload.avoid_items,
        sizes=payload.sizes,
    )

