from pydantic import BaseModel, ConfigDict, Field


class ClosetItemResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    image_id: str
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
