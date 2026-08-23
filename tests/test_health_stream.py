"""스트리밍 진단 엔드포인트.

추천 진행 표시는 서버가 응답을 조금씩 내보낼 수 있어야 의미가 있다.
플랫폼이 버퍼링하면 겉보기 동작은 같고 체감만 사라져, 확인할 길이 필요하다.
"""

import asyncio
import json

from app.api.v1.health import health_stream


def drain() -> list[dict]:
    async def run() -> list[str]:
        response = await health_stream()
        return [chunk async for chunk in response.body_iterator]

    return [json.loads(chunk.removeprefix("data: ").strip()) for chunk in asyncio.run(run())]


def test_probe_emits_several_ticks() -> None:
    assert len(drain()) >= 2


def test_ticks_are_numbered_in_order() -> None:
    assert [event["tick"] for event in drain()] == [1, 2, 3]


def test_ticks_report_elapsed_time_so_buffering_is_visible() -> None:
    # 도착 간격을 볼 수 없는 클라이언트도 이 값으로 판단할 수 있다.
    events = drain()

    assert events[0]["elapsed_ms"] < events[-1]["elapsed_ms"]


def test_probe_carries_no_user_data() -> None:
    # 인증을 걸지 않았다. 본문에 사용자 데이터가 없어야 그 선택이 정당하다.
    assert all(set(event) == {"tick", "elapsed_ms"} for event in drain())


def test_probe_asks_proxies_not_to_buffer() -> None:
    response = asyncio.run(health_stream())

    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"
