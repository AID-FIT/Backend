class VlmService:
    async def analyze(self, image_url: str, prompt: str) -> dict:
        # Vision 팀 모듈이 준비되면 이 메서드 내부만 교체한다.
        lowered = f"{image_url} {prompt}".lower()
        is_clothing = not any(word in lowered for word in ["food", "cat", "dog", "car"])
        return {
            "is_clothing": is_clothing,
            "confidence": 92 if is_clothing else 35,
            "colors": ["화이트", "라이트 블루"],
            "materials": ["코튼", "린넨"],
            "fit": ["오버핏", "와이드"],
            "mood": ["캐주얼", "단정함"],
            "detected_item": "셔츠",
        }

