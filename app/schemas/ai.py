from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DEFAULT_RAG_TOP_K = 30


class AgentError(BaseModel):
    # Internal error shape shared by all agent nodes.
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool
    source: Literal["agent", "vlm", "rag", "llm", "backend"]


class IntentClassification(BaseModel):
    """LLM output used by the graph's first routing decision."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["general_chat", "fashion_service"]
    reason: str | None = None


class QueryRefinement(BaseModel):
    """Standalone search query produced from text, history, and VLM metadata."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)

    @field_validator("query")
    @classmethod
    def require_non_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()


class RetrievalPlan(BaseModel):
    """LLM decision for source routing and prior-result reuse."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["reuse", "retrieve"]
    retrieval_target: Literal["closet", "musinsa", "hybrid"]
    candidate_scope: Literal["all", "shown", "unseen"] = "all"
    selected_item_refs: list[str] = Field(default_factory=list)
    reason: str | None = None


class GeneralChatAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def require_non_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()


class VLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_urls: list[str] = Field(default_factory=list)


class VLMItem(BaseModel):
    # Accept the VLM team's legacy "sense of season" key while using snake_case internally.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

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
    is_fashion_item: bool | None = None


class VLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[VLMItem] = Field(default_factory=list)
    is_fashion_item: bool = True


class RAGRequest(BaseModel):
    # Full request contract passed from the agent to retrieval.
    model_config = ConfigDict(extra="forbid")

    user_id: str
    query: str
    retrieval_target: Literal["closet", "musinsa", "hybrid"]
    user_profile: dict = Field(default_factory=dict)
    vlm_items: list[dict] = Field(default_factory=list)
    closet_items: list[dict] = Field(default_factory=list)
    use_closet_style: bool = True
    filters: dict = Field(default_factory=dict)
    top_k: int = DEFAULT_RAG_TOP_K


class RAGItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str | None = None
    source: Literal["closet", "musinsa"]
    name: str | None = None
    brand: str | None = None
    price: int | None = None
    category: str | None = None
    label: str | None = None
    gender: str | None = None
    image_url: str
    product_url: str | None = None
    color: str | None = None
    material: str | None = None
    fit: str | None = None
    pattern: str | None = None
    mood: str | None = None
    sense_of_season: str | None = None
    similarity_score: float | None = None
    metadata_score: float | None = None
    final_score: float | None = None

    @model_validator(mode="after")
    def require_musinsa_product_url(self) -> "RAGItem":
        # Musinsa cards must be directly clickable in the client.
        if self.source == "musinsa" and not self.product_url:
            raise ValueError("product_url is required for musinsa RAG items")
        return self


class RAGResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RAGItem] = Field(default_factory=list)
    message: str | None = None
