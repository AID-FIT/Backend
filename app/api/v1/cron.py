import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.schemas.recommendation import PendingAnalysisResponse
from app.services.closet_service import ClosetService

router = APIRouter()


def verify_cron_secret(authorization: str | None = Header(default=None)) -> None:
    """Vercel Cron은 CRON_SECRET이 설정돼 있으면 Bearer로 실어 보낸다.

    시크릿이 비어 있으면 엔드포인트를 열지 않는다. 인증 없는 스윕 엔드포인트는
    외부에서 호출해 AI 비용을 태울 수 있다.
    """
    if not settings.cron_secret:
        raise HTTPException(status_code=404, detail="Not Found")

    expected = f"Bearer {settings.cron_secret}"
    # 타이밍 공격을 피하려고 길이와 무관하게 상수 시간으로 비교한다.
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get(
    "/analyze-pending",
    response_model=PendingAnalysisResponse,
    dependencies=[Depends(verify_cron_secret)],
)
async def cron_analyze_pending(db: AsyncSession = Depends(get_db)) -> PendingAnalysisResponse:
    """분석이 남아 있는 사진을 사용자 구분 없이 훑는다.

    클라이언트 재시도는 사용자가 앱을 열어야 동작한다. 이 경로는 그렇지 않은
    잔여분을 회수하는 안전망이다.
    """
    result = await ClosetService().analyze_pending(db, user_id=None)
    return PendingAnalysisResponse(**result)
