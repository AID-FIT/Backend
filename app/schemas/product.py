from pydantic import BaseModel, Field


class ProductResponse(BaseModel):
    id: str
    brand: str
    name: str
    category: str
    price: int | None = None
    image_url: str | None = None
    tags: list[str] = Field(default_factory=list)

