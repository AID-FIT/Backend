class RagService:
    async def search(self, query: str, limit: int = 5) -> list[dict]:
        # RAG 팀의 ChromaDB/FAISS 검색 함수가 준비되면 이 메서드 내부만 교체한다.
        return [
            {
                "item_id": "musinsa_10001",
                "source": "musinsa",
                "item_name": "세미 와이드 데님 팬츠",
                "brand": "Example Brand",
                "category": "pants",
                "price": 39900,
                "image_url": "https://image.musinsa.com/10001.jpg",
                "product_url": "https://www.musinsa.com/products/10001",
                "tags": ["denim", "semi-wide", "minimal"],
            },
            {
                "item_id": "musinsa_10002",
                "source": "musinsa",
                "item_name": "코튼 와이드 치노 팬츠",
                "brand": "AID BASIC",
                "category": "pants",
                "price": 59000,
                "image_url": None,
                "product_url": "https://www.musinsa.com/products/10002",
                "tags": ["cotton", "wide", "casual"],
            },
            {
                "item_id": "musinsa_10003",
                "source": "musinsa",
                "item_name": "화이트 레더 스니커즈",
                "brand": "AID WALK",
                "category": "shoes",
                "price": 69000,
                "image_url": None,
                "product_url": "https://www.musinsa.com/products/10003",
                "tags": ["sneakers", "white", "daily"],
            },
        ][:limit]
