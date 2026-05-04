from app.agent.agent_pipeline import AidFitAgentPipeline


class RecommendationService:
    def __init__(self, pipeline: AidFitAgentPipeline | None = None) -> None:
        self.pipeline = pipeline or AidFitAgentPipeline()

    async def create(self, prompt: str, image_url: str, user_id: str | None, context: dict) -> dict:
        return await self.pipeline.run(
            prompt=prompt,
            image_url=image_url,
            user_id=user_id,
            context=context,
        )

