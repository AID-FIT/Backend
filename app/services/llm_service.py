class LlmService:
    async def compose_recommendation(
        self, query: str, vlm_result: dict, rag_items: list[dict]
    ) -> dict:
        # LLM API 연동 전까지 rag_items만 사용해 deterministic 응답을 만든다.
        recommendations = []
        for product in rag_items[:3]:
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
            "message": "화이트 셔츠에는 미니멀한 세미 와이드 데님 팬츠가 잘 어울립니다.",
            "recommendations": recommendations,
            "style_guide": {
                "summary": "미니멀 캐주얼 코디",
                "tips": [
                    "상의가 밝은 색이므로 하의는 중청 또는 진청 계열이 안정적입니다.",
                    "신발은 화이트 스니커즈를 추천합니다.",
                ],
            },
        }
