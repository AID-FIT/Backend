from pydantic import BaseModel, Field


class ImageUploadResponse(BaseModel):
    id: str
    image_url: str
    content_type: str


class RecommendationCreateRequest(BaseModel):
    prompt: str
    image_url: str
    user_id: str | None = None
    context: dict = Field(default_factory=dict)


class OutfitProduct(BaseModel):
    id: str
    brand: str
    price: int | None = None
    imageUrl: str | None = None


class OutfitItemResponse(BaseModel):
    id: str
    category: str
    name: str
    reason: str
    imageTone: str = "#f5f7fa"
    product: OutfitProduct | None = None


class RecommendationResponse(BaseModel):
    id: str
    title: str
    summary: str
    tags: list[str]
    items: list[OutfitItemResponse]
    vlm_result: dict = Field(default_factory=dict)


class FeedbackEventCreate(BaseModel):
    user_id: str | None = None
    recommendation_id: str | None = None
    product_id: str | None = None
    event_type: str
    metadata: dict = Field(default_factory=dict)


class FeedbackEventResponse(BaseModel):
    id: str
    event_type: str

