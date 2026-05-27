from app.core.config import settings
from app.schemas.ai import VLMResponse


class VlmService:
    def __init__(self, use_mock_ai: bool | None = None) -> None:
        self.use_mock_ai = settings.use_mock_ai if use_mock_ai is None else use_mock_ai

    async def analyze(self, image_url: str | None) -> dict:
        # Preserve the old single-image API by delegating to the batch path.
        response = await self.analyze_many([image_url] if image_url else [])
        if response["items"]:
            item = response["items"][0]
            return {**item, "is_fashion_item": response["is_fashion_item"]}
        return {
            "thumbnail_url": image_url or "mock://no-image",
            "is_fashion_item": True,
        }

    async def analyze_many(self, image_urls: list[str]) -> dict:
        # Mock mode is the default until the real vision service is connected.
        if self.use_mock_ai:
            return await self._mock_analyze_many(image_urls)
        return await self._external_analyze_many(image_urls)

    async def _mock_analyze_many(self, image_urls: list[str]) -> dict:
        items = [self._mock_item(image_url) for image_url in image_urls]
        response = {
            "items": items,
            "is_fashion_item": all(bool(item.get("is_fashion_item")) for item in items) if items else True,
        }
        return VLMResponse.model_validate(response).model_dump()

    async def _external_analyze_many(self, image_urls: list[str]) -> dict:
        # Future adapter boundary: replace this method with POST /vision/analyze.
        return await self._mock_analyze_many(image_urls)

    def _mock_item(self, image_url: str) -> dict:
        # Mark obvious non-fashion test URLs as invalid fashion inputs.
        lowered = image_url.lower()
        is_fashion_item = not any(word in lowered for word in ["food", "cat", "dog", "car", "landscape"])
        return {
            "name": "업로드 의류 이미지",
            "brand": "unknown",
            "price": None,
            "category": "상의",
            "label": "니트",
            "gender": "unisex",
            "thumbnail_url": image_url,
            "product_url": None,
            "color": "white",
            "material": "cotton",
            "fit": "oversized",
            "pattern": "solid",
            "mood": "casual",
            "sense_of_season": "spring",
            "is_fashion_item": is_fashion_item,
        }
