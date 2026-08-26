from collections import OrderedDict
from math import sqrt
from typing import Any, Protocol

from app.services.catalog_matching import clean_value, season_set, split_tokens
from app.services.embedding_service import EmbeddingError, EmbeddingService


STYLE_EMBEDDING_FIELDS = (
    ("color", "color"),
    ("material", "material"),
    ("fit", "fit"),
    ("pattern", "pattern"),
    ("mood", "mood"),
    ("sense_of_season", "season"),
)
DEFAULT_STYLE_CACHE_SIZE = 256


class SimilarityEmbeddingClient(Protocol):
    async def embed_similarity_chunked(
        self,
        texts: list[str],
        chunk_size: int = 100,
    ) -> list[list[float]]: ...


def build_style_embedding_text(item: dict[str, Any]) -> str:
    """Build an embedding input containing taste attributes and nothing commercial."""
    parts: list[str] = []
    for source_field, label in STYLE_EMBEDDING_FIELDS:
        raw_value = item.get(source_field)
        if source_field == "sense_of_season":
            raw_value = raw_value or item.get("season")
            values = season_set(raw_value)
        else:
            values = split_tokens(raw_value)
        if values:
            parts.append(f"{label}: {', '.join(sorted(values))}")

    if parts:
        return "; ".join(parts)

    # Catalog rows can disappear after a like was saved. The snapshot name is the
    # only remaining style clue in that case, so use it strictly as a fallback.
    fallback_name = clean_value(item.get("item_name") or item.get("name"))
    return f"fashion style: {fallback_name}" if fallback_name else ""


def cosine_similarity(left: list[float], right: list[float]) -> float | None:
    if not left or len(left) != len(right):
        return None
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return None
    similarity = sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )
    # Negative similarity is not a positive preference signal.
    return max(0.0, min(1.0, similarity))


class StyleEmbeddingService:
    """Batch and cache style-only embeddings without adding persistent storage."""

    def __init__(
        self,
        embedding_service: SimilarityEmbeddingClient | None = None,
        max_cache_entries: int = DEFAULT_STYLE_CACHE_SIZE,
    ) -> None:
        self.embedding_service = embedding_service or EmbeddingService()
        self.max_cache_entries = max(0, max_cache_entries)
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    async def embed_items(
        self,
        items: list[dict[str, Any]],
    ) -> list[list[float] | None]:
        texts = [build_style_embedding_text(item) for item in items]
        return await self.embed_texts(texts)

    async def embed_texts(self, texts: list[str]) -> list[list[float] | None]:
        resolved = {
            text: self._cache[text]
            for text in dict.fromkeys(texts)
            if text and text in self._cache
        }
        missing = list(
            dict.fromkeys(
                text for text in texts if text and text not in resolved
            )
        )
        if missing:
            vectors = await self.embedding_service.embed_similarity_chunked(missing)
            if len(vectors) != len(missing):
                raise EmbeddingError(
                    f"expected {len(missing)} style embeddings, got {len(vectors)}"
                )
            for text, vector in zip(missing, vectors, strict=True):
                resolved[text] = vector
                self._remember(text, vector)

        results: list[list[float] | None] = []
        for text in texts:
            vector = resolved.get(text) if text else None
            if text in self._cache:
                self._cache.move_to_end(text)
            results.append(vector)
        return results

    def _remember(self, text: str, vector: list[float]) -> None:
        if self.max_cache_entries == 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self.max_cache_entries:
            self._cache.popitem(last=False)


_default_style_embedding_service: StyleEmbeddingService | None = None


def get_style_embedding_service() -> StyleEmbeddingService:
    global _default_style_embedding_service
    if _default_style_embedding_service is None:
        _default_style_embedding_service = StyleEmbeddingService()
    return _default_style_embedding_service
