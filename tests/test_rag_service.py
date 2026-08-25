import asyncio

from app.services.rag_service import RagService
from tests.fake_ai import DeterministicRagService


def closet_item(item_id: str, image_url: str, category: str = "하의") -> dict:
    return {
        "closet_item_id": item_id,
        "name": f"옷장 상품 {item_id}",
        "brand": "내 옷장",
        "price": None,
        "category": category,
        "label": None,
        "gender": "unisex",
        "image_url": image_url,
        "product_url": None,
        "color": "black",
        "material": "cotton",
        "fit": "wide",
        "pattern": "solid",
        "mood": "casual",
        "sense_of_season": "spring",
    }


def rag_request(
    retrieval_target: str,
    closet_items: list[dict],
    vlm_items: list[dict] | None = None,
) -> dict:
    return {
        "user_id": "user_001",
        "query": "이 옷에 어울리는 옷을 옷장에서 찾아줘",
        "retrieval_target": retrieval_target,
        "user_profile": {},
        "vlm_items": vlm_items or [],
        "closet_items": closet_items,
        "use_closet_style": True,
        "filters": {},
        "top_k": 10,
    }


def test_mock_rag_excludes_previously_shown_item_refs_before_limiting() -> None:
    service = DeterministicRagService()

    items = asyncio.run(
        service.search(
            "비슷한 상품 하나 더",
            limit=10,
            excluded_item_refs=[
                "6081171",
                "https://www.musinsa.com/products/6075610",
            ],
        )
    )

    item_ids = {item["item_id"] for item in items}
    assert "6081171" not in item_ids
    assert "6075610" not in item_ids
    assert len(items) == 10


def test_closet_target_returns_only_owned_closet_items() -> None:
    service = DeterministicRagService()
    owned_items = [
        closet_item("closet_001", "https://cdn.aidfit.com/closet_001.jpg"),
        closet_item("closet_002", "https://cdn.aidfit.com/closet_002.jpg", category="신발"),
    ]

    response = asyncio.run(service.search_request(rag_request("closet", owned_items)))

    assert {item["item_id"] for item in response["items"]} == {"closet_001", "closet_002"}
    assert {item["source"] for item in response["items"]} == {"closet"}


def test_closet_target_does_not_fall_back_to_musinsa_when_closet_is_empty() -> None:
    service = DeterministicRagService()

    response = asyncio.run(service.search_request(rag_request("closet", [])))

    assert response["items"] == []


def test_closet_target_excludes_the_attached_reference_item() -> None:
    service = DeterministicRagService()
    reference_url = "https://cdn.aidfit.com/reference.jpg"
    owned_items = [
        closet_item("reference", reference_url, category="상의"),
        closet_item("match", "https://cdn.aidfit.com/match.jpg"),
    ]

    response = asyncio.run(
        service.search_request(
            rag_request("closet", owned_items, vlm_items=[{"thumbnail_url": reference_url}])
        )
    )

    assert [item["item_id"] for item in response["items"]] == ["match"]


def test_hybrid_target_combines_closet_and_musinsa_items() -> None:
    service = DeterministicRagService()
    owned_items = [closet_item("closet_001", "https://cdn.aidfit.com/closet_001.jpg")]

    response = asyncio.run(service.search_request(rag_request("hybrid", owned_items)))

    assert {item["source"] for item in response["items"]} == {"closet", "musinsa"}


def test_external_musinsa_target_uses_vector_catalog(monkeypatch) -> None:
    service = RagService()
    calls = []

    async def fake_vector_search(request) -> list[dict]:
        calls.append(request)
        return [
            {
                "item_id": "vector_001",
                "source": "musinsa",
                "name": "벡터 검색 상품",
                "image_url": "https://cdn.aidfit.com/vector_001.jpg",
                "product_url": "https://www.musinsa.com/products/vector_001",
                "similarity_score": 0.91,
                "metadata_score": 0.4,
                "final_score": 0.7825,
            }
        ]

    monkeypatch.setattr(service, "_search_vector_catalog", fake_vector_search)

    response = asyncio.run(service.search_request(rag_request("musinsa", [])))

    assert len(calls) == 1
    assert response["items"][0]["item_id"] == "vector_001"
    assert response["items"][0]["final_score"] == 0.7825


def test_external_hybrid_target_combines_closet_and_vector_items(monkeypatch) -> None:
    service = RagService()

    async def fake_vector_search(_request) -> list[dict]:
        return [
            {
                "item_id": "vector_001",
                "source": "musinsa",
                "name": "벡터 검색 상품",
                "image_url": "https://cdn.aidfit.com/vector_001.jpg",
                "product_url": "https://www.musinsa.com/products/vector_001",
            }
        ]

    monkeypatch.setattr(service, "_search_vector_catalog", fake_vector_search)
    owned_items = [closet_item("closet_001", "https://cdn.aidfit.com/closet_001.jpg")]

    response = asyncio.run(service.search_request(rag_request("hybrid", owned_items)))

    assert {item["source"] for item in response["items"]} == {"closet", "musinsa"}
