from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import ImageAsset, User
from app.db.session import get_db
from app.schemas.recommendation import ImageUploadResponse
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
    await ClosetService().analyze_and_store(db, current_user, image)
    await db.commit()
    return ImageUploadResponse(
        id=stored["id"],
        image_url=stored["image_url"],
        content_type=stored["content_type"],
    )


@router.get("", response_model=list[ImageUploadResponse])
async def list_images(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ImageUploadResponse]:
    result = await db.execute(
        select(ImageAsset)
        .where(ImageAsset.user_id == current_user.id)
        .order_by(ImageAsset.created_at.desc())
    )
    return [
        ImageUploadResponse(id=image.id, image_url=image.storage_url, content_type=image.content_type)
        for image in result.scalars().all()
    ]
