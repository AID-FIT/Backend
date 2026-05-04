class LlmService:
    async def compose_recommendation(
        self, prompt: str, vlm_result: dict, rag_items: list[dict]
    ) -> dict:
        # LLM API 연동 전까지 rag_items만 사용해 deterministic 응답을 만든다.
        items = []
        reasons = {
            "상의": "이미지에서 보이는 밝고 단정한 무드와 잘 이어지는 상의입니다.",
            "하의": "상의의 여유로운 핏을 자연스럽게 받쳐 주는 균형감 있는 아이템입니다.",
            "신발": "전체 톤을 밝게 정리하고 일상 이동에도 편안합니다.",
        }
        for product in rag_items[:3]:
            category = product["category"]
            items.append(
                {
                    "id": product["id"],
                    "category": category,
                    "name": product["name"],
                    "reason": reasons.get(category, "요청한 분위기와 잘 맞는 추천 아이템입니다."),
                    "imageTone": product.get("imageTone", "#f5f7fa"),
                    "product": {
                        "id": product["id"],
                        "brand": product["brand"],
                        "price": product.get("price"),
                        "imageUrl": product.get("image_url"),
                    },
                }
            )

        return {
            "title": "가볍고 단정한 데이트룩",
            "summary": (
                "VLM이 추출한 밝은 컬러, 여유로운 핏, 캐주얼한 무드를 기준으로 "
                "검색된 상품 안에서만 조합했습니다."
            ),
            "tags": ["캐주얼", "단정함", "데일리"],
            "items": items,
        }

