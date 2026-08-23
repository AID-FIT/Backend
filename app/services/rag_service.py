import asyncio

from app.core.config import settings
from app.schemas.ai import RAGRequest, RAGResponse


class RagService:
    FALLBACK_IMAGE_URL = "https://image.msscdn.net/images/no_image_500.png"

    def __init__(self, use_mock_ai: bool | None = None) -> None:
        self.use_mock_ai = settings.use_mock_ai if use_mock_ai is None else use_mock_ai

    async def search_request(self, rag_request: dict) -> dict:
        # Keep mock and future external retrieval behind the same contract.
        request = RAGRequest.model_validate(rag_request)
        if self.use_mock_ai:
            response = await self._mock_search_request(request)
        else:
            response = await self._external_search_request(request)
        return RAGResponse.model_validate(response).model_dump()

    async def _mock_search_request(self, request: RAGRequest) -> dict:
        excluded_item_refs = request.filters.get("excluded_item_refs") or []
        refresh_seed = int(request.filters.get("refresh_seed") or 0)

        if request.retrieval_target == "closet":
            items = self._search_closet(request, limit=request.top_k, refresh_seed=refresh_seed)
        elif request.retrieval_target == "musinsa":
            items = await self.search(
                request.query,
                limit=request.top_k,
                refresh_seed=refresh_seed,
                excluded_item_refs=excluded_item_refs,
            )
        else:
            closet_items = self._search_closet(
                request,
                limit=request.top_k,
                refresh_seed=refresh_seed,
            )
            catalog_items = await self.search(
                request.query,
                limit=request.top_k,
                refresh_seed=refresh_seed,
                excluded_item_refs=excluded_item_refs,
            )
            items = self._interleave(closet_items, catalog_items)[: request.top_k]

        return {
            "items": [self._normalize_item(item) for item in items],
            "message": "success" if items else "검색 결과가 없습니다.",
        }

    async def _external_search_request(self, request: RAGRequest) -> dict:
        excluded_item_refs = {
            str(item_ref).strip()
            for item_ref in request.filters.get("excluded_item_refs") or []
            if str(item_ref).strip()
        }
        refresh_seed = int(request.filters.get("refresh_seed") or 0)

        if request.retrieval_target == "closet":
            items = self._search_closet(request, limit=request.top_k, refresh_seed=refresh_seed)
        elif request.retrieval_target == "musinsa":
            items = await self._search_vector_catalog(request)
        else:
            closet_items = self._search_closet(
                request,
                limit=request.top_k,
                refresh_seed=refresh_seed,
            )
            catalog_items = await self._search_vector_catalog(request)
            items = self._interleave(closet_items, catalog_items)[: request.top_k]

        normalized_items = [
            self._normalize_item(item)
            for item in items
            if not excluded_item_refs.intersection(
                self._item_refs(item, ("item_id", "product_url", "image_url"))
            )
        ]
        return {
            "items": normalized_items,
            "message": "success" if normalized_items else "검색 결과가 없습니다.",
        }

    async def _search_vector_catalog(self, request: RAGRequest) -> list[dict]:
        # Import lazily so mock-only development does not require the vector stack.
        from rag_service_final import search as vector_search

        musinsa_request = request.model_copy(update={"retrieval_target": "musinsa"})
        response = await asyncio.to_thread(vector_search, musinsa_request.model_dump())
        return response.model_dump()["items"]

    async def search(
        self,
        query: str,
        limit: int = 5,
        refresh_seed: int = 0,
        excluded_item_refs: list[str] | None = None,
    ) -> list[dict]:
        excluded_refs = {
            str(item_ref).strip()
            for item_ref in excluded_item_refs or []
            if str(item_ref).strip()
        }
        catalog = [
            item
            for item in self._mock_catalog()
            if not excluded_refs.intersection(
                str(item.get(key) or "").strip()
                for key in ("item_id", "product_url", "image_url")
                if str(item.get(key) or "").strip()
            )
        ]
        if not catalog or limit <= 0:
            return []

        start = (max(refresh_seed, 0) * limit) % len(catalog)
        return [catalog[(start + offset) % len(catalog)] for offset in range(min(limit, len(catalog)))]

    def _search_closet(
        self,
        request: RAGRequest,
        limit: int,
        refresh_seed: int = 0,
    ) -> list[dict]:
        """Return only owned items supplied by the authenticated backend path."""
        if limit <= 0:
            return []

        excluded_refs = {
            str(item_ref).strip()
            for item_ref in request.filters.get("excluded_item_refs") or []
            if str(item_ref).strip()
        }
        reference_refs = {
            item_ref
            for vlm_item in request.vlm_items
            if isinstance(vlm_item, dict)
            for item_ref in self._item_refs(
                vlm_item,
                ("closet_item_id", "item_id", "product_url", "image_url", "thumbnail_url"),
            )
        }

        candidates: list[dict] = []
        for closet_item in request.closet_items:
            if not isinstance(closet_item, dict):
                continue
            item_refs = self._item_refs(
                closet_item,
                ("closet_item_id", "item_id", "product_url", "image_url"),
            )
            if excluded_refs.intersection(item_refs) or reference_refs.intersection(item_refs):
                continue

            candidate = {
                **closet_item,
                "item_id": closet_item.get("closet_item_id") or closet_item.get("item_id"),
                "source": "closet",
                "name": closet_item.get("name") or closet_item.get("item_name"),
                "final_score": self._closet_relevance_score(closet_item, request),
            }
            candidates.append(candidate)

        candidates.sort(key=lambda item: float(item.get("final_score") or 0.0), reverse=True)
        if not candidates:
            return []
        start = (max(refresh_seed, 0) * limit) % len(candidates)
        return [
            candidates[(start + offset) % len(candidates)]
            for offset in range(min(limit, len(candidates)))
        ]

    def _closet_relevance_score(self, item: dict, request: RAGRequest) -> float:
        query = request.query.casefold()
        score = 0.0
        searchable_fields = (
            "name",
            "brand",
            "category",
            "label",
            "gender",
            "color",
            "material",
            "fit",
            "pattern",
            "mood",
            "sense_of_season",
        )
        for key in searchable_fields:
            value = str(item.get(key) or "").strip().casefold()
            if value and value in query:
                score += 0.2

        for key in ("category", "color", "style", "sense_of_season", "gender"):
            expected = request.filters.get(key)
            actual = item.get(key)
            if expected and actual and str(expected).casefold() == str(actual).casefold():
                score += 0.1

        for vlm_item in request.vlm_items:
            if not isinstance(vlm_item, dict):
                continue
            for key in ("material", "fit", "pattern", "mood", "sense_of_season"):
                reference_value = vlm_item.get(key)
                actual = item.get(key)
                if reference_value and actual and str(reference_value).casefold() == str(actual).casefold():
                    score += 0.05
        return min(score, 1.0)

    @staticmethod
    def _item_refs(item: dict, keys: tuple[str, ...]) -> set[str]:
        return {
            str(item.get(key)).strip()
            for key in keys
            if item.get(key) is not None and str(item.get(key)).strip()
        }

    @staticmethod
    def _interleave(first: list[dict], second: list[dict]) -> list[dict]:
        combined: list[dict] = []
        for index in range(max(len(first), len(second))):
            if index < len(first):
                combined.append(first[index])
            if index < len(second):
                combined.append(second[index])
        return combined

    def _normalize_item(self, item: dict) -> dict:
        # Fill optional catalog fields into the RAG response schema.
        return {
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

    def _mock_catalog(self) -> list[dict]:
        # Temporary Musinsa-like catalog used until vector retrieval is wired.
        return [
            {
                "item_id": "6081171",
                "source": "musinsa",
                "item_name": "[SET] 그래픽 피그먼트 오버핏 반팔티셔츠 VOL.01",
                "brand": "모즈모즈",
                "category": "상의",
                "price": 54400,
                "image_url": "https://image.msscdn.net/images/goods_img/20260304/6081171/6081171_17738881269364_500.jpg",
                "product_url": "https://www.musinsa.com/products/6081171",
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
            },
            {
                "item_id": "6102395",
                "source": "musinsa",
                "item_name": "소프트 그래픽 쇼트 슬리브 티셔츠",
                "brand": "아웃스탠딩",
                "category": "상의",
                "price": 33000,
                "image_url": "https://image.msscdn.net/images/goods_img/20260309/6102395/6102395_17739872421263_500.jpg",
                "product_url": "https://www.musinsa.com/products/6102395",
            },
            {
                "item_id": "6129443",
                "source": "musinsa",
                "item_name": "코리안 레터링 반팔 티셔츠 - NAVY",
                "brand": "마크곤잘레스",
                "category": "상의",
                "price": 46550,
                "image_url": "https://image.msscdn.net/images/goods_img/20260313/6129443/6129443_17737134775461_500.jpg",
                "product_url": "https://www.musinsa.com/products/6129443",
            },
            {
                "item_id": "6086792",
                "source": "musinsa",
                "item_name": "릴랙스 그래픽 반팔 티셔츠",
                "brand": "스파오",
                "category": "상의",
                "price": 25900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260305/6086792/6086792_17727543246534_500.jpg",
                "product_url": "https://www.musinsa.com/products/6086792",
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
            },
            {
                "item_id": "6108783",
                "source": "musinsa",
                "item_name": "Chaser 링거 반팔 티셔츠",
                "brand": "비바스튜디오",
                "category": "상의",
                "price": 35900,
                "image_url": "https://image.msscdn.net/images/goods_img/20260310/6108783/6108783_17742516004008_500.jpg",
                "product_url": "https://www.musinsa.com/products/6108783",
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
            },
            {
                "item_id": "6109669",
                "source": "musinsa",
                "item_name": "Youthful 반팔 티셔츠",
                "brand": "비바스튜디오",
                "category": "상의",
                "price": 28400,
                "image_url": "https://image.msscdn.net/images/goods_img/20260310/6109669/6109669_17731302889705_500.jpg",
                "product_url": "https://www.musinsa.com/products/6109669",
            },
        ]
