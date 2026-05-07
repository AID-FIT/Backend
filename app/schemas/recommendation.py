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
    image_urls: list[str] = Field(default_factory=list)
    closet_item_id: str | None = None
    recommendation_target: str = "musinsa"
    context: dict = Field(default_factory=dict)
    user_profile: dict = Field(default_factory=dict)


class AgentRecommendationItem(BaseModel):
    item_id: str | None = None
    source: Literal["closet", "musinsa"]
    item_name: str | None = None
    brand: str | None = None
    category: str | None = None
    image_url: str
    product_url: str | None = None
    price: int | None = None
    reason: str


class StyleGuide(BaseModel):
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


class AgentError(BaseModel):
    code: str
    message: str
    retryable: bool
    source: Literal["agent", "vlm", "rag", "backend"]


class RecommendationResponse(BaseModel):
    status: Literal["success", "empty", "error"]
    message: str
    recommendations: list[AgentRecommendationItem] = Field(default_factory=list)
    style_guide: StyleGuide | None = None
    error: AgentError | None = None
    vlm_result: dict | None = None
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
