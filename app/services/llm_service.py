class LlmService:
    async def compose_recommendation(
        self, query: str, vlm_result: dict, rag_items: list[dict]
    ) -> dict:
        # LLM API 연동 전까지 rag_items만 사용해 deterministic 응답을 만든다.
        recommendations = []
        for product in rag_items[:5]:
            reason = (
                f"{vlm_result.get('color', '기준 의류')} {vlm_result.get('category', '아이템')}의 "
                f"{vlm_result.get('mood', '스타일')} 무드와 잘 이어지는 {product['category']}입니다."
            )
            recommendations.append(
                {
                    "item_id": product["item_id"],
                    "source": product.get("source", "musinsa"),
                    "item_name": product["item_name"],
                    "brand": product["brand"],
                    "category": product["category"],
                    "image_url": product.get("image_url"),
                    "product_url": product.get("product_url"),
                    "price": product.get("price"),
                    "reason": reason,
                }
            )

        return {
            "status": "success",
            "message": "사용자의 옷장과 취향을 기준으로 오늘 입기 좋은 한 세트 코디를 구성했습니다.",
            "recommendations": recommendations,
            "style_guide": {
                "summary": "오늘의 추천 코디",
                "tips": [
                    "모자, 상의, 하의, 신발 순서로 바로 입을 수 있는 조합을 우선 배치했습니다.",
                    "추가 아이템은 전체 무드를 보완하는 선택지로 활용하세요.",
                ],
            },
        }
