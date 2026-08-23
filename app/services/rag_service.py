from app.core.config import settings
from app.rag.rag_service import search
from app.schemas.ai import RAGRequest, RAGResponse


class RagService:
    def __init__(self, use_mock_ai: bool | None = None) -> None:
        self.use_mock_ai = settings.use_mock_ai if use_mock_ai is None else use_mock_ai

    async def search_request(self, rag_request: dict) -> dict:
        request = RAGRequest.model_validate(rag_request)
        response = search(request.model_dump())
        return RAGResponse.model_validate(response.model_dump()).model_dump()
