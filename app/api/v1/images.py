from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import ClosetItem, ImageAsset, User
from app.db.session import get_db
from app.schemas.recommendation import ImageUploadResponse, PendingAnalysisResponse
from app.services.closet_service import ClosetService
from app.services.storage_service import StorageService

router = APIRouter()


@router.post("", response_model=ImageUploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImageUploadResponse:
    stored = await StorageService().save_upload(file)

    # 저장 경로가 내용 해시라 같은 이미지는 URL이 같다.
    # 이미 등록된 사진이면 행과 VLM 분석을 다시 만들지 않고 기존 것을 돌려준다.
    existing = await db.execute(
        select(ImageAsset).where(
            ImageAsset.user_id == current_user.id,
            ImageAsset.storage_url == stored["image_url"],
        )
    )
    duplicate = existing.scalar_one_or_none()
    if duplicate is not None:
        return ImageUploadResponse(
            id=duplicate.id,
            image_url=duplicate.storage_url,
            content_type=duplicate.content_type,
            analyzed=await ClosetService().has_analysis(db, duplicate),
        )

    image = ImageAsset(
        id=stored["id"],
        user_id=current_user.id,
        storage_url=stored["image_url"],
        content_type=stored["content_type"],
        purpose="closet",
    )
    db.add(image)
    await db.flush()

    # VLM 호출은 여기서 하지 않는다. Gemini Vision이 몇 초를 잡아먹는 동안
    # 사용자가 업로드 완료를 기다리게 된다. 같은 사진이 이미 분석돼 있으면
    # 그 결과만 복사하고(호출 없음), 아니면 클라이언트가 /analyze로 이어서 요청한다.
    reused = await ClosetService().reuse_analysis(db, current_user, image)
    await db.commit()

    return ImageUploadResponse(
        id=stored["id"],
        image_url=stored["image_url"],
        content_type=stored["content_type"],
        analyzed=reused is not None,
    )


@router.post("/{image_id}/analyze", response_model=ImageUploadResponse)
async def analyze_image(
    image_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImageUploadResponse:
    """업로드와 분리된 VLM 분석. 이미 분석된 사진이면 다시 호출하지 않는다."""
    owned = await db.execute(
        select(ImageAsset).where(
            ImageAsset.id == image_id,
            ImageAsset.user_id == current_user.id,
        )
    )
    image = owned.scalar_one_or_none()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    closet_service = ClosetService()
    if not await closet_service.has_analysis(db, image):
        if await closet_service.reuse_analysis(db, current_user, image) is None:
            await closet_service.analyze_and_store(db, current_user, image)
        await db.commit()

    return ImageUploadResponse(
        id=image.id,
        image_url=image.storage_url,
        content_type=image.content_type,
        analyzed=True,
    )


@router.post("/analyze-pending", response_model=PendingAnalysisResponse)
async def analyze_pending_images(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PendingAnalysisResponse:
    """분석이 남아 있는 내 사진을 한 배치만큼 처리한다.

    업로드 직후의 분석 요청이 도달하지 못한 경우를 옷장 진입 시 회수한다.
    한 번에 다 처리하지 않고 has_more로 알린다. VLM 한 건이 수 초라
    함수 실행 시간 안에 끝나는 만큼만 잡는다.
    """
    result = await ClosetService().analyze_pending(db, user_id=current_user.id)
    return PendingAnalysisResponse(**result)


@router.get("", response_model=list[ImageUploadResponse])
async def list_images(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ImageUploadResponse]:
    result = await db.execute(
        select(ImageAsset, ClosetItem.id)
        .outerjoin(ClosetItem, ClosetItem.image_id == ImageAsset.id)
        .where(ImageAsset.user_id == current_user.id)
        .order_by(ImageAsset.created_at.desc())
    )
    return [
        ImageUploadResponse(
            id=image.id,
            image_url=image.storage_url,
            content_type=image.content_type,
            analyzed=closet_item_id is not None,
        )
        for image, closet_item_id in result.all()
    ]


@router.delete("/{image_id}", status_code=204)
async def delete_image(
    image_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    owned = await db.execute(
        select(ImageAsset).where(
            ImageAsset.id == image_id,
            ImageAsset.user_id == current_user.id,
        )
    )
    image = owned.scalar_one_or_none()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    storage_url = image.storage_url
    await db.execute(delete(ClosetItem).where(ClosetItem.image_id == image.id))
    await db.delete(image)
    await db.flush()

    # 저장 경로가 내용 해시라 같은 사진을 올린 다른 사용자와 객체를 공유한다.
    # 아직 참조가 남아 있으면 원본을 지우지 않는다.
    remaining = await db.execute(
        select(func.count()).select_from(ImageAsset).where(ImageAsset.storage_url == storage_url)
    )
    if remaining.scalar_one() == 0:
        await StorageService().delete_by_url(storage_url)

    await db.commit()
    return Response(status_code=204)
