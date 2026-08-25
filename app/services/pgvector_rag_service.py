from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.catalog_matching import (
    build_search_text,
    final_score,
    infer_query_intents,
    metadata_score,
)
from app.services.embedding_service import EmbeddingService
from app.services.target_category import infer_target_category

TABLE = "product_vectors"
FALLBACK_IMAGE_URL = "https://image.msscdn.net/images/no_image_500.png"

# 후보를 넉넉히 뽑아 두고 상위만 돌려준다. 메타데이터 가중치를 얹으면
# 벡터 순위와 최종 순위가 달라지기 때문이다.
CANDIDATE_MULTIPLIER = 4
# 홈 피드는 한 번에 100건을 뽑는다. 상한이 그 두 배에 못 미치면 새로고침이
# 회전할 자리가 없어 같은 상품이 돌기만 한다.
MAX_CANDIDATES = 400
# HNSW가 그래프에서 훑는 폭. pgvector 기본값은 40이고, 그보다 큰 LIMIT을
# 걸어도 인덱스는 40건 언저리만 돌려준다. 운영 DB에서 실측한 결과 LIMIT 400
# 질의가 40건만 반환했고, 그 40건은 아우터·바지에 쏠려 가방과 원피스는 0건이었다.
# 400으로 넓히면 같은 질의가 400건에 7종 전부를 담는다.
MIN_EF_SEARCH = 40


def candidate_limit_for(limit: int) -> int:
    """뽑을 개수보다 넉넉히 훑는다.

    새로고침은 이 후보 안에서 시작점을 옮겨 새 상품을 보여준다(`_rotate`).
    훑는 수가 뽑는 수의 배수가 아니면 회전한 창이 서로 겹쳐, 새로고침해도
    본 상품이 다시 올라온다.
    """
    return min(max(limit * CANDIDATE_MULTIPLIER, limit), MAX_CANDIDATES)


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
        refresh_seed: int = 0,
        vlm_items: list[dict] | None = None,
        request_mode: str = "direct",
        preferred_styles: list[str] | None = None,
        use_preference_search: bool = False,
    ) -> list[dict]:
        if not query.strip():
            return []

        effective_filters = self._effective_filters(
            query,
            filters or {},
            infer_category_from_query=request_mode != "coordination",
        )
        # 검색 임베딩은 요청/사진 의미를 보존한다. 옷장은 ranker 전용이며,
        # 취향도 모호한 일반 추천이라고 판정된 경우에만 보완한다.
        search_text = build_search_text(
            query,
            vlm_items=vlm_items,
            preferred_styles=preferred_styles,
            use_preference_search=use_preference_search,
            request_mode=request_mode,
        )
        vector = await self.embedding_service.embed_query(search_text)
        conditions, params = self._build_conditions(effective_filters)
        candidate_limit = candidate_limit_for(limit)

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

        await self._widen_hnsw_search(db, candidate_limit)
        result = await db.execute(statement, params)
        excluded = excluded_item_refs or set()
        intents = infer_query_intents(query, effective_filters)

        candidates: list[dict] = []
        for row in result.mappings():
            item = self._to_item(dict(row))
            if excluded.intersection({item["item_id"], item.get("product_url") or ""}):
                continue
            self._apply_scores(item, intents, effective_filters)
            candidates.append(item)

        # 벡터 순위와 최종 순위는 다르다. 유사도만으로 자르면 "검정"을 요청해도
        # 색이 전혀 다른 상품이 위에 온다. 넉넉히 뽑아 두고 여기서 다시 세운다.
        candidates = self._deduplicate(candidates)
        candidates.sort(key=lambda item: item.get("final_score") or 0.0, reverse=True)

        return self._rotate(candidates, limit, refresh_seed)[:limit]

    def _apply_scores(
        self,
        item: dict,
        intents: dict[str, set[str]],
        filters: dict[str, Any],
    ) -> None:
        meta = metadata_score(item, intents, filters=filters)
        item["metadata_score"] = round(meta, 4)
        item["final_score"] = round(final_score(item["similarity_score"], meta), 4)

    def _deduplicate(self, items: list[dict]) -> list[dict]:
        """같은 상품 페이지를 가리키는 행을 하나로 줄인다.

        카탈로그에는 한 상품이 여러 행으로 들어 있다(고유 product_url 10,500개,
        전체 12,794행). 그대로 두면 같은 상품이 피드에 두 번 오른다.
        """
        best_by_url: dict[str, dict] = {}
        without_url: list[dict] = []

        for item in items:
            product_url = (item.get("product_url") or "").strip()
            if not product_url:
                without_url.append(item)
                continue
            existing = best_by_url.get(product_url)
            if existing is None or (item.get("final_score") or 0.0) > (existing.get("final_score") or 0.0):
                best_by_url[product_url] = item

        return [*best_by_url.values(), *without_url]

    async def _widen_hnsw_search(self, db: AsyncSession, candidate_limit: int) -> None:
        """이번 질의에서 HNSW가 훑을 폭을 넓힌다.

        `SET LOCAL`이라 이 트랜잭션에서만 유효하다. 세션 단위로 걸면 pgbouncer가
        커넥션을 돌려쓸 때 다른 요청까지 따라 바뀐다.

        pgvector 라이브러리가 아직 로드되지 않은 새 커넥션에서도 동작한다 —
        Postgres가 점이 있는 이름을 placeholder로 받아 두기 때문이다.
        (`SHOW`는 같은 시점에 UndefinedObject를 내지만 `SET`은 통과한다.)
        운영 DB에 직접 붙어 확인했다.
        """
        ef_search = max(candidate_limit, MIN_EF_SEARCH)
        await db.execute(text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))

    def _rotate(self, candidates: list[dict], limit: int, refresh_seed: int) -> list[dict]:
        """새로고침할 때마다 후보 풀 안에서 시작점을 옮긴다.

        이 쿼리는 결정적이다. 같은 질의·같은 필터면 매번 같은 상위 N건이 나와,
        새로고침을 눌러도 화면이 그대로였다. OFFSET으로 건너뛰면 누를수록
        유사도가 낮은 상품만 남으므로, 이미 뽑아 둔 상위 후보 안에서 회전한다.
        """
        if refresh_seed <= 0 or not candidates:
            return candidates

        start = (refresh_seed * max(limit, 1)) % len(candidates)
        return candidates[start:] + candidates[:start]

    def _effective_filters(
        self,
        query: str,
        filters: dict[str, Any],
        infer_category_from_query: bool = True,
    ) -> dict[str, Any]:
        """카테고리가 정해지지 않았으면 질의에서 뽑는다.

        벡터 검색만으로는 "바지에 어울리는 상의"에서 상의를 찾지 못한다.
        질의 텍스트가 바지 설명으로 가득해 비슷한 바지가 먼저 걸린다.
        찾는 옷의 카테고리를 필터로 못박아야 한다.
        """
        if filters.get("category"):
            return filters

        # In coordination mode a category mentioned before "어울리는" describes
        # the reference garment. The Agent supplies a category filter only when
        # the user explicitly named the kind of candidate they want.
        if not infer_category_from_query:
            return filters

        target = infer_target_category(query)
        if not target:
            return filters
        return {**filters, "category": target}

    def _build_conditions(self, filters: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        """메타데이터 선필터. 벡터 검색 전에 후보를 좁힌다.

        `style`과 `preferred_styles`는 여기서 다루지 않는다. `product_vectors`에
        대응하는 컬럼이 없고, 프로필 취향은 ranker에서 반영한다.
        """
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

        mood = self._clean(filters.get("mood"))
        if mood:
            conditions.append(self._multi_value_condition("mood", "mood"))
            params["mood"] = mood

        color = self._clean(filters.get("color"))
        if color:
            conditions.append(self._multi_value_condition("color", "color"))
            params["color"] = color

        season = self._clean(filters.get("season") or filters.get("sense_of_season"))
        # "all"은 사계절 상품이다. 카탈로그의 41%가 여기 속해, 계절을 고를 때
        # 함께 허용하지 않으면 후보가 통째로 사라진다.
        if season and season not in {"all", "all-season"}:
            conditions.append(
                f"({self._multi_value_condition('season', 'season')}"
                " OR lower(season) IN ('all', 'all-season'))"
            )
            params["season"] = season

        price_max = self._to_int(filters.get("price_max") or filters.get("max_price"))
        if price_max is not None:
            conditions.append("(price IS NULL OR price <= :price_max)")
            params["price_max"] = price_max

        price_min = self._to_int(filters.get("price_min") or filters.get("min_price"))
        if price_min is not None:
            conditions.append("(price IS NULL OR price >= :price_min)")
            params["price_min"] = price_min

        return conditions, params

    def _multi_value_condition(self, column: str, key: str) -> str:
        """쉼표로 이어진 다중값 컬럼에서 한 값을 찾는다.

        이 컬럼들은 "casual, street", "black, white", "spring, fall"처럼 여러 값을
        한 문자열에 담고 있다. `= 'casual'`로는 하나도 걸리지 않는다.
        `LIKE '%casual%'`은 반대로 다른 낱말 안에 든 조각까지 잡는다
        ("red"가 "covered"에). 양끝에 구분자를 붙여 낱말 경계를 만든다.
        """
        return f"', ' || lower({column}) || ',' LIKE '%, ' || :{key} || ',%'"

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
