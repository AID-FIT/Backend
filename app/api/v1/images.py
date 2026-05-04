from fastapi import APIRouter, File, UploadFile

from app.schemas.recommendation import ImageUploadResponse
from app.services.storage_service import StorageService

router = APIRouter()


@router.post("", response_model=ImageUploadResponse)
async def upload_image(file: UploadFile = File(...)) -> ImageUploadResponse:
    stored = await StorageService().save_upload(file)
    return ImageUploadResponse(**stored)

