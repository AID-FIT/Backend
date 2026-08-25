from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImageUploadResponse(BaseModel):
    id: str
    image_url: str
    content_type: str
    # 옷 메타데이터 분석이 끝났는지. false면 클라이언트가 /analyze를 이어서 부른다.
    analyzed: bool = False


class PendingAnalysisResponse(BaseModel):
    analyzed: int
    failed: int
    # 한 배치로 다 끝나지 않았다는 뜻. 호출부가 이어서 부를지 판단한다.
    has_more: bool


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age_group: str | None = None
    preferred_styles: list[str] = Field(default_factory=list)
    gender: str | None = None
    height_cm: int | None = None


class ClosetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    closet_item_id: str
    name: str | None = None
    brand: str | None = None
    price: int | None = None
    category: str | None = None
    label: str | None = None
    gender: str | None = None
    image_url: str | None = None
    product_url: str | None = None
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


class AppliedFilters(BaseModel):
    """이번 추천에 실제로 걸린 조건.

    화면이 "무엇으로 찾았는지"를 사용자에게 보여주기 위한 것이다. 조건을
    되짚을 수 없으면 결과가 왜 이런지 알 방법이 없다.
    """

    model_config = ConfigDict(extra="forbid")

    category: str | None = None
    mood: str | None = None
    season: str | None = None
    age_range: str | None = None
    gender: str | None = None
    preferred_styles: list[str] = Field(default_factory=list)
    prompt: str = ""
    result_count: int = 0


class RecommendationResponse(AgentResponse):
    """HTTP 응답. 에이전트 계약(AgentResponse)에 화면용 정보를 얹는다.

    AgentResponse는 LLM 출력 검증에도 쓰이므로 그쪽은 건드리지 않는다.
    """

    applied_filters: AppliedFilters | None = None


class FeedbackEventCreate(BaseModel):
    user_id: str | None = None
    recommendation_id: str | None = None
    product_id: str | None = None
    event_type: str
    metadata: dict = Field(default_factory=dict)


class FeedbackEventResponse(BaseModel):
    id: str
    event_type: str
