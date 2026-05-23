class RagService:
    FALLBACK_IMAGE_URL = "https://image.msscdn.net/images/no_image_500.png"

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
                "item_id": "6081171",
                "source": "musinsa",
                "item_name": "[SET] 그래픽 피그먼트 오버핏 반팔티셔츠 VOL.01",
                "brand": "모즈모즈",
                "category": "상의",
                "price": 54400,
                "image_url": "https://image.msscdn.net/images/goods_img/20260304/6081171/6081171_17738881269364_500.jpg",
                "product_url": "https://www.musinsa.com/products/6081171",
                "tags": ["20% 할인", "반소매 티셔츠"],
            },
            {
                "item_id": "6075610",
                "source": "musinsa",
                "item_name": "CRACKED STAR HALF T BEIGE",
                "brand": "메종미네드",
                "category": "상의",
                "price": 38700,
                "image_url": "https://image.msscdn.net/images/goods_img/20260303/6075610/6075610_17733148086242_500.jpg",
                "product_url": "https://www.musinsa.com/products/6075610",
                "tags": ["10% 할인", "반소매 티셔츠"],
            },
            {
                "item_id": "6103287",
                "source": "musinsa",
                "item_name": "와플 헨리넥 데일리 반팔 티셔츠 - 2color",
                "brand": "마인드브릿지",
                "category": "상의",
                "price": 29900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260309/6103287/6103287_17750284664520_500.jpg",
                "product_url": "https://www.musinsa.com/products/6103287",
                "tags": ["40% 할인", "반소매 티셔츠"],
            },
            {
                "item_id": "6125368",
                "source": "musinsa",
                "item_name": "[JURASSIC WORLD] Mosasaurus Tee_VTG Black",
                "brand": "파르티멘토 리웍스",
                "category": "상의",
                "price": 49900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260312/6125368/6125368_17744981268200_500.jpg",
                "product_url": "https://www.musinsa.com/products/6125368",
                "tags": ["20% 할인", "반소매 티셔츠"],
            },
            {
                "item_id": "6084669",
                "source": "musinsa",
                "item_name": "NYC LOCATION T-SHIRT (23COLOR) (LRAMCTR702P)",
                "brand": "그루브라임",
                "category": "상의",
                "price": 9900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260305/6084669/6084669_17726862036016_500.jpg",
                "product_url": "https://www.musinsa.com/products/6084669",
                "tags": ["66% 할인", "반소매 티셔츠"],
            },
            {
                "item_id": "6102395",
                "source": "musinsa",
                "item_name": "[수아레x패션플래닛] 에어리 크롭 워셔블 하프 니트",
                "brand": "수아레",
                "category": "상의",
                "price": 33000,
                "image_url": "https://image.msscdn.net/images/goods_img/20260309/6102395/6102395_17739872421263_500.jpg",
                "product_url": "https://www.musinsa.com/products/6102395",
                "tags": ["33% 할인", "반소매 티셔츠"],
            },
            {
                "item_id": "6129443",
                "source": "musinsa",
                "item_name": "코리아 에디션 타이거 반팔 티셔츠 - NAVY",
                "brand": "마크곤잘레스",
                "category": "상의",
                "price": 46550,
                "image_url": "https://image.msscdn.net/images/goods_img/20260313/6129443/6129443_17737134775461_500.jpg",
                "product_url": "https://www.musinsa.com/products/6129443",
                "tags": ["5% 할인", "반소매 티셔츠"],
            },
            {
                "item_id": "6086792",
                "source": "musinsa",
                "item_name": "[리락쿠마] 반팔 티셔츠(MIX)_SPRLG25U02",
                "brand": "스파오",
                "category": "상의",
                "price": 25900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260305/6086792/6086792_17727543246534_500.jpg",
                "product_url": "https://www.musinsa.com/products/6086792",
                "tags": ["반소매 티셔츠"],
            },
            {
                "item_id": "6125389",
                "source": "musinsa",
                "item_name": "[BACK TO THE FUTURE] Duo Tee_VTG Black",
                "brand": "파르티멘토 리웍스",
                "category": "상의",
                "price": 49900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260312/6125389/6125389_17744979667151_500.jpg",
                "product_url": "https://www.musinsa.com/products/6125389",
                "tags": ["20% 할인", "반소매 티셔츠"],
            },
            {
                "item_id": "6108783",
                "source": "musinsa",
                "item_name": "Chaser 링거 반팔 티셔츠",
                "brand": "비바라비다",
                "category": "상의",
                "price": 35900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260310/6108783/6108783_17742516004008_500.jpg",
                "product_url": "https://www.musinsa.com/products/6108783",
                "tags": ["23% 할인", "반소매 티셔츠"],
            },
            {
                "item_id": "6107195",
                "source": "musinsa",
                "item_name": "PENGUIN CHARACTER T-SHIRTS (LRPMCTA419M)",
                "brand": "그루브라임",
                "category": "상의",
                "price": 8750,
                "image_url": "https://image.msscdn.net/images/goods_img/20260310/6107195/6107195_17731192461772_500.jpg",
                "product_url": "https://www.musinsa.com/products/6107195",
                "tags": ["65% 할인", "반소매 티셔츠"],
            },
            {
                "item_id": "6109669",
                "source": "musinsa",
                "item_name": "Youthful 반팔 티셔츠",
                "brand": "비바라비다",
                "category": "상의",
                "price": 28400,
                "image_url": "https://image.msscdn.net/images/goods_img/20260310/6109669/6109669_17731302889705_500.jpg",
                "product_url": "https://www.musinsa.com/products/6109669",
                "tags": ["29% 할인", "반소매 티셔츠"],
            },
        ]

        if not catalog or limit <= 0:
            return []

        if outfit_set:
            outfit_sets = [
                ["6107195", "6129443", "6081171", "6103287", "6084669"],
                ["6107195", "6108783", "6102395", "6086792", "6109669"],
                ["6107195", "6125368", "6075610", "6103287", "6125389"],
            ]
            catalog_by_id = {item["item_id"]: item for item in catalog}
            selected_ids = outfit_sets[max(refresh_seed, 0) % len(outfit_sets)]
            return [catalog_by_id[item_id] for item_id in selected_ids if item_id in catalog_by_id][:limit]

        start = (max(refresh_seed, 0) * limit) % len(catalog)
        return [catalog[(start + offset) % len(catalog)] for offset in range(min(limit, len(catalog)))]

    async def search_request(self, rag_request: dict) -> dict:
        items = await self.search(
            rag_request.get("query", ""),
            limit=int(rag_request.get("top_k") or 10),
            refresh_seed=int((rag_request.get("filters") or {}).get("refresh_seed") or 0),
            outfit_set=bool((rag_request.get("filters") or {}).get("outfit_set")),
        )
        return {
            "items": [
                {
                    "item_id": item.get("item_id"),
                    "source": item.get("source", "musinsa"),
                    "name": item.get("name") or item.get("item_name"),
                    "brand": item.get("brand"),
                    "price": item.get("price"),
                    "category": item.get("category"),
                    "label": item.get("label"),
                    "gender": item.get("gender"),
                    "image_url": item.get("image_url") or self.FALLBACK_IMAGE_URL,
                    "product_url": item.get("product_url"),
                    "color": item.get("color"),
                    "material": item.get("material"),
                    "fit": item.get("fit"),
                    "pattern": item.get("pattern"),
                    "mood": item.get("mood"),
                    "sense_of_season": item.get("sense_of_season"),
                    "similarity_score": item.get("similarity_score"),
                    "metadata_score": item.get("metadata_score"),
                    "final_score": item.get("final_score"),
                }
                for item in items
            ],
            "message": "success" if items else "검색 결과가 없습니다.",
        }
