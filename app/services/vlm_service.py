class VlmService:
    async def analyze(self, image_url: str | None, query: str) -> dict:
        # Vision 팀 모듈이 준비되면 이 메서드 내부만 교체한다.
        image_value = image_url or "mock://no-image"
        lowered = f"{image_value} {query}".lower()
        is_match = not any(word in lowered for word in ["food", "cat", "dog", "car"])
        return {
            "name": "업로드 의류 이미지",
            "brand": "unknown",
            "price": None,
            "category": "상의",
            "sub_category": "셔츠",
            "gender": "unisex",
            "image_url": image_value,
            "product_url": None,
            "color": "white",
            "material": "cotton",
            "fit": "oversized",
            "pattern": "solid",
            "mood": "casual",
            "sense of season": "spring",
            "is_match": is_match,
        }

    async def analyze_many(self, image_urls: list[str], query: str) -> dict:
        results = [await self.analyze(image_url, query) for image_url in image_urls]
        if not results:
            return await self.analyze(None, query)

        primary = results[0]
        primary["items"] = results
        primary["image_urls"] = image_urls
        primary["is_match"] = any(bool(result.get("is_match")) for result in results)
        return primary
