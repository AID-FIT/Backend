class RagService:
    async def search(self, query: str, limit: int = 5) -> list[dict]:
        # RAG 팀의 ChromaDB/FAISS 검색 함수가 준비되면 이 메서드 내부만 교체한다.
        return [
            {
                "id": "prod_shirt_001",
                "brand": "AID BASIC",
                "name": "린넨 오버 셔츠",
                "category": "상의",
                "price": 39900,
                "image_url": None,
                "imageTone": "#f5f7fa",
                "tags": ["린넨", "오버핏", "화이트"],
            },
            {
                "id": "prod_denim_001",
                "brand": "AID DENIM",
                "name": "연청 와이드 데님",
                "category": "하의",
                "price": 59000,
                "image_url": None,
                "imageTone": "rgba(0,112,209,0.08)",
                "tags": ["와이드", "데님", "라이트 블루"],
            },
            {
                "id": "prod_shoes_001",
                "brand": "AID WALK",
                "name": "화이트 스니커즈",
                "category": "신발",
                "price": 69000,
                "image_url": None,
                "imageTone": "#f3f3f3",
                "tags": ["스니커즈", "화이트", "캐주얼"],
            },
        ][:limit]

