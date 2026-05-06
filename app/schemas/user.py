from pydantic import BaseModel, Field


class UserProfileResponse(BaseModel):
    id: str
    email: str | None = None
    nickname: str
    role: str
    age_range: str | None = None
    styles: list[str] = Field(default_factory=list)
    preferred_colors: list[str] = Field(default_factory=list)
    avoid_items: list[str] = Field(default_factory=list)
    sizes: dict = Field(default_factory=dict)


class UserPreferenceUpdate(BaseModel):
    age_range: str | None = None
    styles: list[str] = Field(default_factory=list)
    preferred_colors: list[str] = Field(default_factory=list)
    avoid_items: list[str] = Field(default_factory=list)
    sizes: dict = Field(default_factory=dict)


class OnboardingCompleteRequest(UserPreferenceUpdate):
    closet_image_ids: list[str] = Field(default_factory=list)
