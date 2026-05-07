class VlmService:
    async def analyze(self, image_url: str | None) -> dict:
        # Vision 팀 모듈이 준비되면 이 메서드 내부만 교체한다.
        image_value = image_url or "mock://no-image"
        lowered = image_value.lower()
        is_fashion_item = not any(word in lowered for word in ["food", "cat", "dog", "car", "landscape"])
        return {
            "name": "업로드 의류 이미지",
            "brand": "unknown",
            "price": None,
            "category": "상의",
            "label": "셔츠",
            "gender": "unisex",
            "thumbnail_url": image_value,
            "product_url": None,
            "color": "white",
            "material": "cotton",
            "fit": "oversized",
            "pattern": "solid",
            "mood": "casual",
            "sense_of_season": "spring",
            "is_fashion_item": is_fashion_item,
        }

    async def analyze_many(self, image_urls: list[str]) -> dict:
        items = [await self.analyze(image_url) for image_url in image_urls]
        return {
            "items": items,
            "is_fashion_item": all(bool(item.get("is_fashion_item")) for item in items) if items else True,
        }
