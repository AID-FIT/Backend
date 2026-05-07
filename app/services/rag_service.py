class RagService:
    async def search(
        self,
        query: str,
        limit: int = 5,
        refresh_seed: int = 0,
        outfit_set: bool = False,
    ) -> list[dict]:
        # RAG 팀의 ChromaDB/FAISS 검색 함수가 준비되면 이 메서드 내부만 교체한다.
        catalog = [
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
            {
                "item_id": "musinsa_10004",
                "source": "musinsa",
                "item_name": "라이트웨이트 나일론 셔츠",
                "brand": "AID OUTER",
                "category": "shirt",
                "price": 49000,
                "image_url": None,
                "product_url": "https://www.musinsa.com/products/10004",
                "tags": ["nylon", "light", "spring"],
            },
            {
                "item_id": "musinsa_10005",
                "source": "musinsa",
                "item_name": "미니멀 크로스백",
                "brand": "AID BAG",
                "category": "bag",
                "price": 42000,
                "image_url": None,
                "product_url": "https://www.musinsa.com/products/10005",
                "tags": ["minimal", "black", "daily"],
            },
            {
                "item_id": "musinsa_10006",
                "source": "musinsa",
                "item_name": "린넨 블렌드 와이드 팬츠",
                "brand": "AID SUMMER",
                "category": "pants",
                "price": 65000,
                "image_url": None,
                "product_url": "https://www.musinsa.com/products/10006",
                "tags": ["linen", "wide", "summer"],
            },
            {
                "item_id": "musinsa_10007",
                "source": "musinsa",
                "item_name": "그래픽 포인트 반팔 티셔츠",
                "brand": "AID STREET",
                "category": "top",
                "price": 35000,
                "image_url": None,
                "product_url": "https://www.musinsa.com/products/10007",
                "tags": ["graphic", "street", "summer"],
            },
            {
                "item_id": "musinsa_10008",
                "source": "musinsa",
                "item_name": "클래식 로퍼",
                "brand": "AID WALK",
                "category": "shoes",
                "price": 79000,
                "image_url": None,
                "product_url": "https://www.musinsa.com/products/10008",
                "tags": ["loafer", "classic", "date"],
            },
            {
                "item_id": "musinsa_10009",
                "source": "musinsa",
                "item_name": "워시드 데님 자켓",
                "brand": "AID DENIM",
                "category": "outer",
                "price": 89000,
                "image_url": None,
                "product_url": "https://www.musinsa.com/products/10009",
                "tags": ["denim", "casual", "layer"],
            },
            {
                "item_id": "musinsa_10010",
                "source": "musinsa",
                "item_name": "소프트 코튼 니트",
                "brand": "AID KNIT",
                "category": "top",
                "price": 54000,
                "image_url": None,
                "product_url": "https://www.musinsa.com/products/10010",
                "tags": ["knit", "soft", "minimal"],
            },
            {
                "item_id": "musinsa_10011",
                "source": "musinsa",
                "item_name": "톤온톤 볼캡",
                "brand": "AID CAP",
                "category": "cap",
                "price": 29000,
                "image_url": None,
                "product_url": "https://www.musinsa.com/products/10011",
                "tags": ["cap", "tone-on-tone", "casual"],
            },
            {
                "item_id": "musinsa_10012",
                "source": "musinsa",
                "item_name": "슬림 벨트",
                "brand": "AID ACC",
                "category": "accessory",
                "price": 25000,
                "image_url": None,
                "product_url": "https://www.musinsa.com/products/10012",
                "tags": ["belt", "classic", "minimal"],
            },
        ]

        if not catalog or limit <= 0:
            return []

        if outfit_set:
            outfit_sets = [
                ["musinsa_10011", "musinsa_10007", "musinsa_10001", "musinsa_10003", "musinsa_10005"],
                ["musinsa_10011", "musinsa_10010", "musinsa_10006", "musinsa_10008", "musinsa_10012"],
                ["musinsa_10011", "musinsa_10004", "musinsa_10002", "musinsa_10003", "musinsa_10009"],
            ]
            catalog_by_id = {item["item_id"]: item for item in catalog}
            selected_ids = outfit_sets[max(refresh_seed, 0) % len(outfit_sets)]
            return [catalog_by_id[item_id] for item_id in selected_ids if item_id in catalog_by_id][:limit]

        start = (max(refresh_seed, 0) * limit) % len(catalog)
        return [catalog[(start + offset) % len(catalog)] for offset in range(min(limit, len(catalog)))]
