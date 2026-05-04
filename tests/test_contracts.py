from app.schemas.recommendation import RecommendationResponse


def test_recommendation_response_contract() -> None:
    response = RecommendationResponse(
        id="rec_1",
        title="테스트 추천",
        summary="요약",
        tags=["캐주얼"],
        items=[],
    )

    assert response.model_dump()["id"] == "rec_1"

