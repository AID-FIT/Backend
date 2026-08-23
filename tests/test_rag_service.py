import asyncio

from app.services.rag_service import RagService


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
    service = RagService(use_mock_ai=True)

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
    service = RagService(use_mock_ai=True)
    owned_items = [
        closet_item("closet_001", "https://cdn.aidfit.com/closet_001.jpg"),
        closet_item("closet_002", "https://cdn.aidfit.com/closet_002.jpg", category="신발"),
    ]

    response = asyncio.run(service.search_request(rag_request("closet", owned_items)))

    assert {item["item_id"] for item in response["items"]} == {"closet_001", "closet_002"}
    assert {item["source"] for item in response["items"]} == {"closet"}


def test_closet_target_does_not_fall_back_to_musinsa_when_closet_is_empty() -> None:
    service = RagService(use_mock_ai=True)

    response = asyncio.run(service.search_request(rag_request("closet", [])))

    assert response["items"] == []


def test_closet_target_excludes_the_attached_reference_item() -> None:
    service = RagService(use_mock_ai=True)
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
    service = RagService(use_mock_ai=True)
    owned_items = [closet_item("closet_001", "https://cdn.aidfit.com/closet_001.jpg")]

    response = asyncio.run(service.search_request(rag_request("hybrid", owned_items)))

    assert {item["source"] for item in response["items"]} == {"closet", "musinsa"}
