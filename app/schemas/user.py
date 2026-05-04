from pydantic import BaseModel, Field


class UserProfileResponse(BaseModel):
    id: str
    email: str
    nickname: str
    styles: list[str] = Field(default_factory=list)
    preferred_colors: list[str] = Field(default_factory=list)
    avoid_items: list[str] = Field(default_factory=list)
    sizes: dict = Field(default_factory=dict)


class UserPreferenceUpdate(BaseModel):
    styles: list[str] = Field(default_factory=list)
    preferred_colors: list[str] = Field(default_factory=list)
    avoid_items: list[str] = Field(default_factory=list)
    sizes: dict = Field(default_factory=dict)

