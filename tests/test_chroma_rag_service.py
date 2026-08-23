from rag_service_final import RAGRequest, search_musinsa


class FakeCollection:
    def query(self, **kwargs) -> dict:
        return {
            "metadatas": [
                [
                    {
                        "item_id": "excluded",
                        "name": "제외 상품",
                        "thumbnail_url": "https://cdn.aidfit.com/excluded.jpg",
                        "product_url": "https://www.musinsa.com/products/excluded",
                    },
                    {
                        "item_id": "included",
                        "name": "추천 상품",
                        "thumbnail_url": "https://cdn.aidfit.com/included.jpg",
                        "product_url": "https://www.musinsa.com/products/included",
                    },
                ]
            ],
            "distances": [[0.1, 0.2]],
        }


def test_vector_rag_request_defaults_to_thirty_candidates() -> None:
    request = RAGRequest(
        user_id="user_001",
        query="가을 옷 추천해줘",
        retrieval_target="musinsa",
    )

    assert request.top_k == 30


def test_vector_search_excludes_previously_shown_items() -> None:
    request = RAGRequest(
        user_id="user_001",
        query="상의 추천",
        retrieval_target="musinsa",
        filters={"excluded_item_refs": ["excluded"]},
        top_k=10,
    )

    items = search_musinsa(request, collection=FakeCollection())

    assert [item.item_id for item in items] == ["included"]
