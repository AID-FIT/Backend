from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.embedding_service import EmbeddingService
from app.services.target_category import infer_target_category

TABLE = "product_vectors"
FALLBACK_IMAGE_URL = "https://image.msscdn.net/images/no_image_500.png"

# 후보를 넉넉히 뽑아 두고 상위만 돌려준다. 메타데이터 가중치를 얹으면
# 벡터 순위와 최종 순위가 달라지기 때문이다.
CANDIDATE_MULTIPLIER = 4
MAX_CANDIDATES = 200


class PgVectorRagService:
    """Supabase pgvector에 올린 상품 카탈로그를 검색한다.

    로컬 ChromaDB를 대체한다. 데이터가 DB에 있으므로 배포 번들이 가벼워지고,
    상품을 추가해도 재배포가 필요 없다.
    """

    def __init__(self, embedding_service: EmbeddingService | None = None) -> None:
        self.embedding_service = embedding_service or EmbeddingService()

    async def search(
        self,
        db: AsyncSession,
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
        excluded_item_refs: set[str] | None = None,
    ) -> list[dict]:
        if not query.strip():
            return []

        vector = await self.embedding_service.embed_query(query)
        conditions, params = self._build_conditions(self._effective_filters(query, filters or {}))
        candidate_limit = min(max(limit * CANDIDATE_MULTIPLIER, limit), MAX_CANDIDATES)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        statement = text(
            f"""
            SELECT item_id, source, name, brand, category, label, gender, color,
                   material, fit, pattern, mood, season, price, image_url, product_url,
                   1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
            FROM {TABLE}
            {where_clause}
            ORDER BY embedding <=> CAST(:query_vector AS vector)
            LIMIT :candidate_limit
            """
        )
        params.update({"query_vector": str(vector), "candidate_limit": candidate_limit})

        result = await db.execute(statement, params)
        excluded = excluded_item_refs or set()

        items: list[dict] = []
        for row in result.mappings():
            item = self._to_item(dict(row))
            if excluded.intersection({item["item_id"], item.get("product_url") or ""}):
                continue
            items.append(item)
            if len(items) >= limit:
                break
        return items

    def _effective_filters(self, query: str, filters: dict[str, Any]) -> dict[str, Any]:
        """카테고리가 정해지지 않았으면 질의에서 뽑는다.

        벡터 검색만으로는 "바지에 어울리는 상의"에서 상의를 찾지 못한다.
        질의 텍스트가 바지 설명으로 가득해 비슷한 바지가 먼저 걸린다.
        찾는 옷의 카테고리를 필터로 못박아야 한다.
        """
        if filters.get("category"):
            return filters

        target = infer_target_category(query)
        if not target:
            return filters
        return {**filters, "category": target}

    def _build_conditions(self, filters: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        """메타데이터 선필터. 벡터 검색 전에 후보를 좁힌다."""
        conditions: list[str] = []
        params: dict[str, Any] = {}

        category = self._clean(filters.get("category"))
        if category:
            conditions.append("category = :category")
            params["category"] = category

        gender = self._clean(filters.get("gender"))
        # unisex 요청은 성별을 좁히지 않는다.
        if gender and gender not in {"unisex", "all"}:
            conditions.append("(gender IS NULL OR gender = :gender OR gender = 'unisex')")
            params["gender"] = gender

        price_max = self._to_int(filters.get("price_max") or filters.get("max_price"))
        if price_max is not None:
            conditions.append("(price IS NULL OR price <= :price_max)")
            params["price_max"] = price_max

        price_min = self._to_int(filters.get("price_min") or filters.get("min_price"))
        if price_min is not None:
            conditions.append("(price IS NULL OR price >= :price_min)")
            params["price_min"] = price_min

        return conditions, params

    def _to_item(self, row: dict) -> dict:
        similarity = float(row.get("similarity") or 0.0)
        return {
            "item_id": row["item_id"],
            "source": row.get("source") or "musinsa",
            "name": row.get("name"),
            "item_name": row.get("name"),
            "brand": row.get("brand"),
            "category": row.get("category"),
            "label": row.get("label"),
            "gender": row.get("gender"),
            "color": row.get("color"),
            "material": row.get("material"),
            "fit": row.get("fit"),
            "pattern": row.get("pattern"),
            "mood": row.get("mood"),
            "sense_of_season": row.get("season"),
            "price": row.get("price"),
            "image_url": row.get("image_url") or FALLBACK_IMAGE_URL,
            "product_url": row.get("product_url"),
            "similarity_score": round(similarity, 4),
            "metadata_score": None,
            "final_score": round(similarity, 4),
        }

    @staticmethod
    def _clean(value: Any) -> str | None:
        text_value = str(value or "").strip().lower()
        return text_value or None

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def pgvector_enabled() -> bool:
    return settings.rag_vector_backend == "pgvector"
