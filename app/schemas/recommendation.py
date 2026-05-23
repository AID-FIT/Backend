from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImageUploadResponse(BaseModel):
    id: str
    image_url: str
    content_type: str


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age_group: str | None = None
    preferred_styles: list[str] = Field(default_factory=list)


class ClosetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    closet_item_id: str
    category: str | None = None
    color: str | None = None
    material: str | None = None
    fit: str | None = None
    pattern: str | None = None
    mood: str | None = None
    sense_of_season: str | None = None


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    query: str
    image_urls: list[str] = Field(default_factory=list)
    closet_items: list[ClosetItem] = Field(default_factory=list)
    use_closet_style: bool = True
    user_profile: UserProfile | None = None


RecommendationCreateRequest = AgentRequest


class RecommendationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str | None = None
    source: Literal["closet", "musinsa"]
    item_name: str | None = None
    brand: str | None = None
    category: str | None = None
    image_url: str
    product_url: str | None = None
    price: int | None = None
    reason: str

    @model_validator(mode="after")
    def require_musinsa_product_url(self) -> "RecommendationItem":
        if self.source == "musinsa" and not self.product_url:
            raise ValueError("product_url is required for musinsa recommendations")
        return self


class StyleGuide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    tips: list[str] = Field(default_factory=list)


class VlmItemAnalysis(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    brand: str | None = None
    price: int | None = None
    category: str | None = None
    label: str | None = None
    gender: str | None = None
    thumbnail_url: str | None = None
    product_url: str | None = None
    color: str | None = None
    material: str | None = None
    fit: str | None = None
    pattern: str | None = None
    mood: str | None = None
    sense_of_season: str | None = Field(default=None, alias="sense of season")


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "empty", "error"]
    message: str
    recommendations: list[RecommendationItem] = Field(default_factory=list)
    style_guide: StyleGuide | None = None


RecommendationResponse = AgentResponse


class FeedbackEventCreate(BaseModel):
    user_id: str | None = None
    recommendation_id: str | None = None
    product_id: str | None = None
    event_type: str
    metadata: dict = Field(default_factory=dict)


class FeedbackEventResponse(BaseModel):
    id: str
    event_type: str
