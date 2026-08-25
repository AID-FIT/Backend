from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 한 번에 돌려줄 좋아요 수. 목록 화면이 한 페이지에 담을 만큼만 준다.
DEFAULT_LIKE_PAGE_SIZE = 100
MAX_LIKE_PAGE_SIZE = 200
# 하트 상태용 식별자는 훨씬 가벼워 넉넉히 준다.
MAX_LIKED_REFS = 1000


class ProductLikeCreate(BaseModel):
    """좋아요를 누를 때 함께 보내는 상품 정보.

    나중에 목록을 그릴 때 카탈로그를 다시 검색하지 않도록, 화면이 이미 들고
    있는 값을 그대로 받아 저장한다.
    """

    model_config = ConfigDict(extra="forbid")

    # 소유자는 액세스 토큰에서만 온다. product_ref는 서버가 정한다.
    item_id: str | None = None
    source: Literal["closet", "musinsa"]
    item_name: str | None = Field(default=None, max_length=255)
    brand: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    price: int | None = None
    image_url: str | None = Field(default=None, max_length=2048)
    product_url: str | None = Field(default=None, max_length=2048)


class ProductLikeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_ref: str
    source: str
    name: str | None = None
    brand: str | None = None
    category: str | None = None
    price: int | None = None
    image_url: str | None = None
    product_url: str | None = None
    created_at: datetime


class LikedRefsResponse(BaseModel):
    """하트를 채울지 판단하는 데만 쓰는 가벼운 목록."""

    product_refs: list[str] = Field(default_factory=list)
