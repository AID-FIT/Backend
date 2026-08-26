import asyncio

import httpx
import pytest

from app.services.embedding_service import (
    TASK_SEMANTIC_SIMILARITY,
    EmbeddingError,
    EmbeddingService,
)


class FlakyService(EmbeddingService):
    """_embed_batch_once만 갈아끼워 재시도 흐름만 본다."""

    def __init__(self, outcomes: list) -> None:
        super().__init__()
        self.outcomes = outcomes
        self.attempts = 0
        self.task_types: list[str] = []

    async def _embed_batch_once(self, texts, task_type):
        self.task_types.append(task_type)
        outcome = self.outcomes[self.attempts]
        self.attempts += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    # 재시도 간 대기까지 실제로 기다리면 테스트가 느려진다.
    async def instant(_seconds):
        return None

    monkeypatch.setattr("app.services.embedding_service.asyncio.sleep", instant)


def test_transient_failure_is_retried_until_it_succeeds() -> None:
    vectors = [[0.1, 0.2]]
    service = FlakyService(
        [EmbeddingError("connection reset", retryable=True), vectors]
    )

    result = asyncio.run(service.embed_batch(["검은 재킷"]))

    assert result == vectors
    assert service.attempts == 2


def test_configuration_errors_are_not_retried() -> None:
    # 4xx는 기다려도 풀리지 않는다. 즉시 올려야 원인이 드러난다.
    service = FlakyService([EmbeddingError("400 bad request", retryable=False)])

    with pytest.raises(EmbeddingError):
        asyncio.run(service.embed_batch(["검은 재킷"]))

    assert service.attempts == 1


def test_retries_give_up_after_the_attempt_limit() -> None:
    service = FlakyService([EmbeddingError("reset", retryable=True)] * 4)

    with pytest.raises(EmbeddingError):
        asyncio.run(service.embed_batch(["검은 재킷"], max_attempts=4))

    assert service.attempts == 4


def test_empty_input_skips_the_api_entirely() -> None:
    service = FlakyService([])

    assert asyncio.run(service.embed_batch([])) == []
    assert service.attempts == 0


def test_style_similarity_uses_the_symmetric_embedding_task() -> None:
    service = FlakyService([[[0.1, 0.2]]])

    result = asyncio.run(service.embed_for_similarity(["blue cotton street"]))

    assert result == [[0.1, 0.2]]
    assert service.task_types == [TASK_SEMANTIC_SIMILARITY]


def test_network_errors_are_marked_retryable(monkeypatch) -> None:
    # httpx 연결 오류는 메시지가 비어 있는 경우가 있어 타입을 함께 남긴다.
    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ReadError("")

    monkeypatch.setattr("app.services.embedding_service.httpx.AsyncClient", lambda **_: FailingClient())
    monkeypatch.setattr("app.services.embedding_service.settings.gemini_api_key", "test-key")

    service = EmbeddingService()
    with pytest.raises(EmbeddingError) as exc:
        asyncio.run(service._embed_batch_once(["검은 재킷"], "RETRIEVAL_DOCUMENT"))

    assert exc.value.retryable is True
    assert "ReadError" in str(exc.value)
