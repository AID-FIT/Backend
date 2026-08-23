import asyncio

import pytest
from fastapi import HTTPException

from app.api.v1.cron import verify_cron_secret
from app.core import config as config_module
from app.services.closet_service import ClosetService


class FakeImage:
    def __init__(self, image_id: str, user_id: str = "user_001") -> None:
        self.id = image_id
        self.user_id = user_id
        self.storage_url = f"https://cdn.example/{image_id}.png"


class RecordingSession:
    """analyze_pending의 커밋/롤백 흐름만 관찰하는 최소 더블."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class StubClosetService(ClosetService):
    """DB 대신 미리 정한 목록을 돌려주고, 분석 성공/실패를 지정한다."""

    def __init__(self, batches: list[list[FakeImage]], failing_ids: set[str] | None = None) -> None:
        super().__init__(vlm_service=None)
        self.batches = batches
        self.failing_ids = failing_ids or set()
        self.analyzed_ids: list[str] = []
        self.reuse_checked: list[str] = []

    async def list_pending_images(self, db, user_id=None, limit=3):  # type: ignore[override]
        return self.batches.pop(0) if self.batches else []

    async def reuse_analysis_for(self, db, user_id, image):  # type: ignore[override]
        self.reuse_checked.append(image.id)
        return None

    async def analyze_and_store_for(self, db, user_id, image):  # type: ignore[override]
        if image.id in self.failing_ids:
            raise RuntimeError("vlm failed")
        self.analyzed_ids.append(image.id)
        return object()


def test_analyze_pending_processes_every_image_in_the_batch() -> None:
    service = StubClosetService(batches=[[FakeImage("a"), FakeImage("b")], []])
    db = RecordingSession()

    result = asyncio.run(service.analyze_pending(db, user_id="user_001"))

    assert service.analyzed_ids == ["a", "b"]
    assert result == {"analyzed": 2, "failed": 0, "has_more": False}


def test_one_failure_does_not_stop_the_rest() -> None:
    # 한 장이 실패해도 나머지는 이어서 처리해야 한다.
    service = StubClosetService(
        batches=[[FakeImage("a"), FakeImage("b"), FakeImage("c")], []],
        failing_ids={"b"},
    )
    db = RecordingSession()

    result = asyncio.run(service.analyze_pending(db, user_id="user_001"))

    assert service.analyzed_ids == ["a", "c"]
    assert result["analyzed"] == 2
    assert result["failed"] == 1
    assert db.rollbacks == 1


def test_has_more_is_true_when_images_remain() -> None:
    service = StubClosetService(batches=[[FakeImage("a")], [FakeImage("b")]])
    db = RecordingSession()

    result = asyncio.run(service.analyze_pending(db, user_id="user_001"))

    assert result["has_more"] is True


def test_reuse_is_attempted_before_calling_the_model() -> None:
    # 같은 사진이 이미 분석돼 있으면 VLM을 부르지 않아야 한다.
    service = StubClosetService(batches=[[FakeImage("a")], []])
    db = RecordingSession()

    asyncio.run(service.analyze_pending(db, user_id="user_001"))

    assert service.reuse_checked == ["a"]


def test_cron_endpoint_is_closed_when_secret_is_unset(monkeypatch) -> None:
    # 인증 없는 스윕 엔드포인트는 외부에서 호출해 AI 비용을 태울 수 있다.
    monkeypatch.setattr(config_module.settings, "cron_secret", "", raising=False)

    with pytest.raises(HTTPException) as exc:
        verify_cron_secret(authorization="Bearer anything")

    assert exc.value.status_code == 404


def test_cron_endpoint_rejects_a_wrong_secret(monkeypatch) -> None:
    monkeypatch.setattr(config_module.settings, "cron_secret", "correct-secret", raising=False)

    with pytest.raises(HTTPException) as exc:
        verify_cron_secret(authorization="Bearer wrong-secret")

    assert exc.value.status_code == 401


def test_cron_endpoint_rejects_a_missing_header(monkeypatch) -> None:
    monkeypatch.setattr(config_module.settings, "cron_secret", "correct-secret", raising=False)

    with pytest.raises(HTTPException) as exc:
        verify_cron_secret(authorization=None)

    assert exc.value.status_code == 401


def test_cron_endpoint_accepts_the_matching_secret(monkeypatch) -> None:
    monkeypatch.setattr(config_module.settings, "cron_secret", "correct-secret", raising=False)

    assert verify_cron_secret(authorization="Bearer correct-secret") is None
