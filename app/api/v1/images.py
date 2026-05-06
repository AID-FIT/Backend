from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import ImageAsset, User
from app.db.session import get_db
from app.schemas.recommendation import ImageUploadResponse
from app.services.storage_service import StorageService

router = APIRouter()


@router.post("", response_model=ImageUploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImageUploadResponse:
    stored = await StorageService().save_upload(file)
    db.add(
        ImageAsset(
            id=stored["id"],
            user_id=current_user.id,
            storage_url=stored["image_url"],
            content_type=stored["content_type"],
            purpose="closet",
        )
    )
    await db.commit()
    return ImageUploadResponse(**stored)


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
