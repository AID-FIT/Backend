import asyncio

import pytest

from app.services.embedding_service import EmbeddingError
from app.services.style_embedding_service import (
    StyleEmbeddingService,
    build_style_embedding_text,
    cosine_similarity,
)


class FakeSimilarityEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed_similarity_chunked(
        self,
        texts: list[str],
        chunk_size: int = 100,
    ) -> list[list[float]]:
        self.calls.append(list(texts))
        return [
            [float(index + 1), 1.0]
            for index, _text in enumerate(texts)
        ]


def test_style_text_contains_only_taste_attributes() -> None:
    text = build_style_embedding_text(
        {
            "name": "Example Brand 스트릿 팬츠",
            "brand": "Example Brand",
            "category": "바지",
            "price": 99000,
            "color": "Blue",
            "material": "Cotton",
            "fit": "Oversized",
            "pattern": "Graphic",
            "mood": "Street",
            "sense_of_season": "여름",
        }
    )

    assert text == (
        "color: blue; material: cotton; fit: oversized; pattern: graphic; "
        "mood: street; season: summer"
    )
    assert "Example Brand" not in text
    assert "바지" not in text
    assert "99000" not in text


def test_saved_name_is_used_only_when_style_metadata_is_missing() -> None:
    assert build_style_embedding_text({"name": "블루 오버핏 스트릿 셔츠"}) == (
        "fashion style: 블루 오버핏 스트릿 셔츠"
    )


def test_repeated_style_text_is_embedded_once_and_then_cached() -> None:
    client = FakeSimilarityEmbeddingClient()
    service = StyleEmbeddingService(client, max_cache_entries=10)
    items = [
        {"color": "blue", "mood": "street"},
        {"mood": "street", "color": "blue"},
    ]

    first = asyncio.run(service.embed_items(items))
    second = asyncio.run(service.embed_items(items))

    assert client.calls == [["color: blue; mood: street"]]
    assert first == second == [[1.0, 1.0], [1.0, 1.0]]


def test_current_batch_survives_even_when_it_is_larger_than_the_cache() -> None:
    client = FakeSimilarityEmbeddingClient()
    service = StyleEmbeddingService(client, max_cache_entries=1)

    vectors = asyncio.run(
        service.embed_items(
            [{"color": "blue"}, {"color": "red"}, {"color": "black"}]
        )
    )

    assert vectors == [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]]


def test_invalid_embedding_count_is_rejected() -> None:
    class MissingVectorClient(FakeSimilarityEmbeddingClient):
        async def embed_similarity_chunked(self, texts, chunk_size=100):
            return []

    with pytest.raises(EmbeddingError):
        asyncio.run(
            StyleEmbeddingService(MissingVectorClient()).embed_items(
                [{"color": "blue"}]
            )
        )


def test_cosine_similarity_is_bounded_and_handles_invalid_vectors() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([1.0], [-1.0]) == 0.0
    assert cosine_similarity([], []) is None
    assert cosine_similarity([1.0], [1.0, 0.0]) is None
