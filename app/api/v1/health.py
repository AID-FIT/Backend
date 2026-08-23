import asyncio
import json
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()

# 진단용 스트림이 보낼 틱 수와 간격. 실제 추천 스트림과 같은 헤더를 쓴다.
_PROBE_TICKS = 3
_PROBE_INTERVAL_SECONDS = 1.0


@router.get("/health")
async def health_check() -> dict:
    return {"status": "ok"}


@router.get("/health/stream")
async def health_stream() -> StreamingResponse:
    """스트리밍이 실제로 흐르는지 확인하는 진단 엔드포인트.

    추천 진행 표시(`/recommendations/home/stream`)는 서버가 응답을 조금씩
    내보낼 수 있어야 의미가 있다. 그런데 배포 플랫폼이나 중간 프록시가
    응답을 통째로 모았다가 보내면 겉보기 동작은 같고 체감만 사라진다.

    여기에 인증을 걸지 않는 이유는, 확인해야 할 것이 **전송 방식뿐**이고
    본문에 사용자 데이터가 하나도 없기 때문이다. 틱 번호와 경과 시간만 보낸다.

        curl -N https://<host>/api/v1/health/stream

    틱이 1초 간격으로 도착하면 스트리밍이 살아 있는 것이고,
    3초 뒤 한꺼번에 쏟아지면 어딘가에서 버퍼링되고 있는 것이다.
    """

    async def ticks():
        started_at = time.monotonic()
        for tick in range(1, _PROBE_TICKS + 1):
            payload = {"tick": tick, "elapsed_ms": round((time.monotonic() - started_at) * 1000)}
            yield f"data: {json.dumps(payload)}\n\n"
            if tick < _PROBE_TICKS:
                await asyncio.sleep(_PROBE_INTERVAL_SECONDS)

    return StreamingResponse(
        ticks(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
