from app.agent.agent_pipeline import AidFitAgentPipeline


class RecommendationService:
    def __init__(self, pipeline: AidFitAgentPipeline | None = None) -> None:
        self.pipeline = pipeline or AidFitAgentPipeline()

    async def create(
        self,
        query: str,
        image_url: str,
        user_id: str | None,
        closet_item_id: str | None,
        recommendation_target: str,
        context: dict,
    ) -> dict:
        return await self.pipeline.run(
            query=query,
            image_url=image_url,
            user_id=user_id,
            closet_item_id=closet_item_id,
            recommendation_target=recommendation_target,
            context=context,
        )
