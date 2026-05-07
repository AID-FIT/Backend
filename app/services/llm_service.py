class LlmService:
    async def compose_recommendation(
        self,
        query: str,
        vlm_items: list[dict],
        ranked_items: list[dict],
        retrieval_target: str = "musinsa",
    ) -> dict:
        if not ranked_items:
            return {
                "status": "empty",
                "message": "조건에 맞는 추천 상품을 찾지 못했습니다.",
                "recommendations": [],
                "style_guide": None,
            }

        base_item = vlm_items[0] if vlm_items else {}
        recommendations = []
        for product in ranked_items[:5]:
            category = product.get("category")
            reason = (
                f"{base_item.get('color', '사용자 스타일')} {base_item.get('category', '아이템')}의 "
                f"{base_item.get('mood', '무드')}와 잘 이어지는 {category or '아이템'}입니다."
            )
            recommendations.append(
                {
                    "item_id": product.get("item_id"),
                    "source": product.get("source", "musinsa"),
                    "item_name": product.get("item_name") or product.get("name"),
                    "brand": product.get("brand"),
                    "category": category,
                    "image_url": product["image_url"],
                    "product_url": product.get("product_url"),
                    "price": product.get("price"),
                    "reason": reason,
                }
            )

        return {
            "status": "success",
            "message": "사용자 요청과 스타일 정보를 바탕으로 어울리는 코디를 구성했습니다.",
            "recommendations": recommendations,
            "style_guide": {
                "summary": "오늘의 추천 코디" if retrieval_target == "hybrid" else "맞춤 추천 코디",
                "tips": [
                    "추천 리스트의 상품 안에서만 조합했습니다.",
                    "색상과 무드가 충돌하지 않도록 안정적인 아이템을 우선 배치했습니다.",
                ],
            },
        }
