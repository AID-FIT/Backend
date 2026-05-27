from app.agent.agent_pipeline import AidFitAgentPipeline


class RecommendationService:
    def __init__(self, pipeline: AidFitAgentPipeline | None = None) -> None:
        self.pipeline = pipeline or AidFitAgentPipeline()

    async def create(
        self,
        query: str,
        user_id: str,
        image_urls: list[str] | None = None,
        closet_items: list[dict] | None = None,
        use_closet_style: bool = True,
        user_profile: dict | None = None,
        context: dict | None = None,
        recommendation_target: str = "musinsa",
        image_url: str | None = None,
        closet_item_id: str | None = None,
    ) -> dict:
        # Centralize image normalization before entering the LangGraph pipeline.
        normalized_image_urls = image_urls or ([image_url] if image_url else [])
        return await self.pipeline.run(
            query=query,
            user_id=user_id,
            image_urls=normalized_image_urls,
            closet_items=closet_items or [],
            use_closet_style=use_closet_style,
            user_profile=user_profile or {},
            context=context or {},
            recommendation_target=recommendation_target,
            image_url=image_url or (normalized_image_urls[0] if normalized_image_urls else None),
            closet_item_id=closet_item_id,
        )
