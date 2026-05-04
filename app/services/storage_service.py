from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import settings


class StorageService:
    async def save_upload(self, file: UploadFile) -> dict:
        suffix = Path(file.filename or "").suffix or ".bin"
        file_name = f"{uuid4().hex}{suffix}"
        target = settings.upload_dir / file_name
        content = await file.read()
        target.write_bytes(content)
        return {
            "id": file_name.rsplit(".", 1)[0],
            "image_url": f"{settings.public_base_url}/uploads/{file_name}",
            "content_type": file.content_type or "application/octet-stream",
        }

