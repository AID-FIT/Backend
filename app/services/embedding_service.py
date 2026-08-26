import asyncio

import httpx

from app.core.config import settings

# 색인과 질의는 서로 다른 taskType을 써야 검색 품질이 나온다.
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"
TASK_SEMANTIC_SIMILARITY = "SEMANTIC_SIMILARITY"


class EmbeddingError(RuntimeError):
    """임베딩 API 호출이 실패했다."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class EmbeddingService:
    """Gemini 임베딩 API 클라이언트.

    로컬 모델(sentence-transformers)은 PyTorch를 끌고 와 서버리스 함수 크기
    한도를 넘긴다. HTTP로 부르는 임베딩만 배포 환경에서 쓸 수 있다.
    """

    def __init__(self, timeout_seconds: float | None = None) -> None:
        self.timeout_seconds = timeout_seconds or settings.gemini_timeout_seconds

    @property
    def dimensions(self) -> int:
        return settings.embedding_dimensions

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text], task_type=TASK_QUERY)
        return vectors[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self.embed_batch(texts, task_type=TASK_DOCUMENT)

    async def embed_for_similarity(self, texts: list[str]) -> list[list[float]]:
        """Embed symmetric texts for direct semantic-similarity comparison."""
        return await self.embed_batch(texts, task_type=TASK_SEMANTIC_SIMILARITY)

    async def embed_batch(
        self,
        texts: list[str],
        task_type: str = TASK_DOCUMENT,
        max_attempts: int = 4,
    ) -> list[list[float]]:
        """일시적인 실패는 물러났다가 다시 시도한다.

        대량 색인 중 연결이 한 번 끊기면 전체가 멈춘다. 끊김과 5xx, 429는
        기다렸다 재시도하고, 4xx(설정 오류)는 즉시 올린다.
        """
        if not texts:
            return []

        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                return await self._embed_batch_once(texts, task_type)
            except EmbeddingError as exc:
                if not exc.retryable or attempt == max_attempts - 1:
                    raise
                last_error = exc
                await asyncio.sleep(2**attempt)

        raise EmbeddingError(f"embedding failed after {max_attempts} attempts: {last_error}")

    async def _embed_batch_once(self, texts: list[str], task_type: str) -> list[list[float]]:
        if not texts:
            return []
        if not settings.gemini_api_key:
            raise EmbeddingError("GEMINI_API_KEY is not configured")

        base_url = settings.gemini_base_url.rstrip("/")
        model = settings.embedding_model
        url = f"{base_url}/models/{model}:batchEmbedContents"
        payload = {
            "requests": [
                {
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                    "outputDimensionality": settings.embedding_dimensions,
                }
                for text in texts
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": settings.gemini_api_key,
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            # 연결 끊김·타임아웃은 메시지가 비어 있는 경우가 있어 타입을 함께 남긴다.
            raise EmbeddingError(
                f"embedding request failed: {type(exc).__name__}: {exc}", retryable=True
            ) from exc

        if response.is_error:
            # 429(쿼터)와 5xx는 기다리면 풀린다. 나머지 4xx는 설정 문제라 즉시 올린다.
            retryable = response.status_code == 429 or response.status_code >= 500
            raise EmbeddingError(
                f"embedding request failed: {response.status_code} {response.text[:200]}",
                retryable=retryable,
            )

        embeddings = response.json().get("embeddings") or []
        if len(embeddings) != len(texts):
            raise EmbeddingError(f"expected {len(texts)} embeddings, got {len(embeddings)}")

        vectors = [item.get("values") or [] for item in embeddings]
        for vector in vectors:
            if len(vector) != settings.embedding_dimensions:
                raise EmbeddingError(
                    f"expected {settings.embedding_dimensions}-dim vector, got {len(vector)}"
                )
        return vectors

    async def embed_documents_chunked(
        self,
        texts: list[str],
        chunk_size: int = 100,
        delay_seconds: float = 0.0,
    ) -> list[list[float]]:
        """대량 색인용. 배치 요청에도 상한이 있어 잘라서 보낸다."""
        return await self._embed_chunked(
            texts,
            task_type=TASK_DOCUMENT,
            chunk_size=chunk_size,
            delay_seconds=delay_seconds,
        )

    async def embed_similarity_chunked(
        self,
        texts: list[str],
        chunk_size: int = 100,
    ) -> list[list[float]]:
        """스타일처럼 서로 대칭인 문장을 의미 유사도용으로 나눠 임베딩한다."""
        return await self._embed_chunked(
            texts,
            task_type=TASK_SEMANTIC_SIMILARITY,
            chunk_size=chunk_size,
        )

    async def _embed_chunked(
        self,
        texts: list[str],
        *,
        task_type: str,
        chunk_size: int,
        delay_seconds: float = 0.0,
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), chunk_size):
            chunk = texts[start : start + chunk_size]
            vectors.extend(await self.embed_batch(chunk, task_type=task_type))
            if delay_seconds and start + chunk_size < len(texts):
                await asyncio.sleep(delay_seconds)
        return vectors
