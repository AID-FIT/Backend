from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ImageUploadResponse(BaseModel):
    id: str
    image_url: str
    content_type: str


class RecommendationCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str | None = None
    query: str = Field(validation_alias=AliasChoices("query", "prompt"))
    image_url: str | None = None
    closet_item_id: str | None = None
    recommendation_target: str = "musinsa"
    context: dict = Field(default_factory=dict)


class AgentRecommendationItem(BaseModel):
    item_id: str
    source: str
    item_name: str
    brand: str
    category: str
    image_url: str | None = None
    product_url: str | None = None
    price: int | None = None
    reason: str


class StyleGuide(BaseModel):
    summary: str
    tips: list[str] = Field(default_factory=list)


class VlmItemAnalysis(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    brand: str
    price: int | None = None
    category: str
    sub_category: str | None = None
    gender: str | None = None
    image_url: str
    product_url: str | None = None
    color: str | None = None
    material: str | None = None
    fit: str | None = None
    pattern: str | None = None
    mood: str | None = None
    sense_of_season: str | None = Field(default=None, alias="sense of season")
    is_match: bool


class RecommendationResponse(BaseModel):
    status: Literal["success", "fallback", "error"]
    message: str
    recommendations: list[AgentRecommendationItem] = Field(default_factory=list)
    style_guide: StyleGuide
    vlm_result: VlmItemAnalysis | None = None
    request_id: str | None = None


class FeedbackEventCreate(BaseModel):
    user_id: str | None = None
    recommendation_id: str | None = None
    product_id: str | None = None
    event_type: str
    metadata: dict = Field(default_factory=dict)


class FeedbackEventResponse(BaseModel):
    id: str
    event_type: str
