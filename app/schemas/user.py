from pydantic import BaseModel, Field, field_validator

from app.core.gender import normalize_gender

# 사람 키의 상식 범위. DB의 CHECK 제약(ck_user_preferences_height_cm)과 같은 값이다.
MIN_HEIGHT_CM = 100
MAX_HEIGHT_CM = 250


class UserProfileResponse(BaseModel):
    id: str
    email: str | None = None
    nickname: str
    role: str
    age_range: str | None = None
    gender: str | None = None
    height_cm: int | None = None
    styles: list[str] = Field(default_factory=list)
    preferred_colors: list[str] = Field(default_factory=list)
    avoid_items: list[str] = Field(default_factory=list)
    sizes: dict = Field(default_factory=dict)


class UserPreferenceUpdate(BaseModel):
    age_range: str | None = None
    # 화면은 "남성"을 보내고 카탈로그는 "men"을 쓴다. 경계에서 한 번 맞춰
    # DB에는 정규형만 들어가게 한다.
    gender: str | None = None
    height_cm: int | None = Field(default=None, ge=MIN_HEIGHT_CM, le=MAX_HEIGHT_CM)
    styles: list[str] = Field(default_factory=list)
    preferred_colors: list[str] = Field(default_factory=list)
    avoid_items: list[str] = Field(default_factory=list)
    sizes: dict = Field(default_factory=dict)

    @field_validator("gender", mode="before")
    @classmethod
    def _normalize_gender(cls, value: object) -> str | None:
        return normalize_gender(value)


class OnboardingCompleteRequest(UserPreferenceUpdate):
    closet_image_ids: list[str] = Field(default_factory=list)
