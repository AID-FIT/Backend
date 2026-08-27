from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent_pipeline import AidFitAgentPipeline
from app.agent.home_workflow import HomeRecommendationWorkflow
from app.db.models import (
    Recommendation,
    RecommendationItem,
    RecommendationRequest,
    VlmAnalysis,
)


class RecommendationService:
    def __init__(
        self,
        pipeline: AidFitAgentPipeline | None = None,
        home_workflow: HomeRecommendationWorkflow | None = None,
    ) -> None:
        self._pipeline = pipeline
        self.home_workflow = home_workflow or HomeRecommendationWorkflow(
            pipeline.nodes if pipeline is not None else None
        )

    @property
    def pipeline(self) -> AidFitAgentPipeline:
        # Home endpoints do not need to compile or execute the common Agent graph.
        # Other recommendation paths initialize it lazily with the same shared nodes.
        if self._pipeline is None:
            self._pipeline = AidFitAgentPipeline(self.home_workflow.nodes)
        return self._pipeline

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
        lock_retrieval_target: bool = False,
        diversify_by_category: bool = False,
        max_recommendations: int | None = None,
        image_url: str | None = None,
        closet_item_id: str | None = None,
        chat_history: list[dict] | None = None,
        previous_rag_results: list[dict] | None = None,
        previous_shown_item_refs: list[str] | None = None,
        previous_rag_query: str | None = None,
        previous_retrieval_target: str | None = None,
        return_trace: bool = False,
    ) -> dict:
        """공통 Agent 그래프로 비영속 추천을 생성한다."""
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
            lock_retrieval_target=lock_retrieval_target,
            diversify_by_category=diversify_by_category,
            max_recommendations=max_recommendations,
            image_url=image_url or (normalized_image_urls[0] if normalized_image_urls else None),
            closet_item_id=closet_item_id,
            chat_history=chat_history or [],
            previous_rag_results=previous_rag_results or [],
            previous_shown_item_refs=previous_shown_item_refs or [],
            previous_rag_query=previous_rag_query,
            previous_retrieval_target=previous_retrieval_target,
            return_trace=return_trace,
        )

    def stream(self, **kwargs) -> AsyncIterator[dict]:
        """진행 이벤트를 흘리며 추천을 만든다.

        `create`와 같은 인자를 받되 결과를 한 번에 돌려주지 않는다.
        `return_trace`는 받지 않는다 — 스트림의 마지막 이벤트가 항상 트레이스다.
        """
        normalized_image_urls = kwargs.pop("image_urls", None) or (
            [kwargs["image_url"]] if kwargs.get("image_url") else []
        )
        kwargs["image_urls"] = normalized_image_urls
        kwargs["image_url"] = kwargs.get("image_url") or (
            normalized_image_urls[0] if normalized_image_urls else None
        )
        return self.pipeline.stream(**kwargs)

    async def create_home(
        self,
        query: str,
        user_id: str,
        closet_items: list[dict] | None = None,
        use_closet_style: bool = True,
        user_profile: dict | None = None,
        context: dict | None = None,
        diversify_by_category: bool = False,
        max_recommendations: int | None = None,
        return_trace: bool = False,
    ) -> dict:
        """Create a home recommendation through the fixed, non-Agent path."""
        return await self.home_workflow.run(
            query=query,
            user_id=user_id,
            closet_items=closet_items or [],
            use_closet_style=use_closet_style,
            user_profile=user_profile or {},
            context=context or {},
            diversify_by_category=diversify_by_category,
            max_recommendations=max_recommendations,
            return_trace=return_trace,
        )

    def stream_home(self, **kwargs) -> AsyncIterator[dict]:
        """Stream only the stages actually executed by the fixed home path."""
        return self.home_workflow.stream(**kwargs)

    async def create_and_persist(
        self,
        db: AsyncSession,
        user_id: str,
        query: str,
        image_urls: list[str] | None = None,
        closet_items: list[dict] | None = None,
        use_closet_style: bool = True,
        user_profile: dict | None = None,
        context: dict | None = None,
        recommendation_target: str = "musinsa",
        lock_retrieval_target: bool = False,
        diversify_by_category: bool = False,
        max_recommendations: int | None = None,
        return_trace: bool = False,
    ) -> dict:
        """추천을 생성하고 요청/분석/추천/아이템을 DB에 저장한 뒤 응답을 반환한다.

        return_trace=True면 응답 대신 파이프라인 트레이스 전체(검색·랭킹 후보 포함)를
        돌려준다. RAG 평가용이다.
        """
        normalized_image_urls = image_urls or []
        trace = await self.pipeline.run(
            query=query,
            user_id=user_id,
            image_urls=normalized_image_urls,
            closet_items=closet_items or [],
            use_closet_style=use_closet_style,
            user_profile=user_profile or {},
            context=context or {},
            recommendation_target=recommendation_target,
            lock_retrieval_target=lock_retrieval_target,
            diversify_by_category=diversify_by_category,
            max_recommendations=max_recommendations,
            return_trace=True,
        )
        await self._persist(db, user_id, query, trace)
        await db.commit()
        return trace if return_trace else trace["response"]

    async def _persist(self, db: AsyncSession, user_id: str, query: str, trace: dict) -> None:
        response = trace.get("response") or {}
        error = trace.get("error") or {}

        request = RecommendationRequest(
            user_id=user_id,
            prompt=query,
            status=response.get("status", "pending"),
            error_code=error.get("code"),
        )
        db.add(request)
        await db.flush()

        vlm_items = trace.get("vlm_items") or []
        if vlm_items:
            db.add(
                VlmAnalysis(
                    request_id=request.id,
                    result={"items": vlm_items},
                    is_clothing=True,
                    confidence=0,
                )
            )

        recommendations = response.get("recommendations") or []
        style_guide = response.get("style_guide")
        summary = style_guide.get("summary") if isinstance(style_guide, dict) else None

        recommendation = Recommendation(
            request_id=request.id,
            title=summary or response.get("message", "추천 결과"),
            summary=response.get("message", ""),
            tags=[item["category"] for item in recommendations if item.get("category")][:5],
            raw_agent_output=response,
        )
        db.add(recommendation)
        await db.flush()

        for rank, item in enumerate(recommendations):
            db.add(
                RecommendationItem(
                    recommendation_id=recommendation.id,
                    product_id=None,
                    category=item.get("category") or "unknown",
                    reason=item.get("reason") or "",
                    rank=rank,
                )
            )

    async def get_by_id(self, db: AsyncSession, recommendation_id: str, user_id: str) -> dict | None:
        """저장된 추천을 소유자 검증과 함께 조회한다. 없으면 None."""
        result = await db.execute(
            select(Recommendation)
            .join(RecommendationRequest, Recommendation.request_id == RecommendationRequest.id)
            .where(
                Recommendation.id == recommendation_id,
                RecommendationRequest.user_id == user_id,
            )
        )
        recommendation = result.scalar_one_or_none()
        if recommendation is None:
            return None
        return recommendation.raw_agent_output
