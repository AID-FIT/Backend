from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import HTTPException, UploadFile

from app.core.config import settings


class StorageService:
    async def save_upload(self, file: UploadFile) -> dict:
        suffix = Path(file.filename or "").suffix or ".bin"
        file_name = f"{uuid4().hex}{suffix}"
        content = await file.read()
        content_type = file.content_type or "application/octet-stream"

        # 서버리스에서는 로컬 디스크가 요청 간 유지되지 않으므로 Supabase Storage를 쓴다.
        if settings.supabase_storage_enabled:
            image_url = await self._upload_to_supabase(file_name, content, content_type)
        else:
            target = settings.upload_dir / file_name
            target.write_bytes(content)
            image_url = f"{settings.public_base_url}/uploads/{file_name}"

        return {
            "id": file_name.rsplit(".", 1)[0],
            "image_url": image_url,
            "content_type": content_type,
        }

    async def _upload_to_supabase(self, file_name: str, content: bytes, content_type: str) -> str:
        base_url = settings.supabase_url.rstrip("/")
        bucket = settings.supabase_storage_bucket
        # 신형 sb_secret_ 키는 apikey 헤더로 인증한다. 구형 service_role JWT도 두 헤더를 함께 받는다.
        headers = {
            "apikey": settings.supabase_service_key,
            "Authorization": f"Bearer {settings.supabase_service_key}",
            "Content-Type": content_type,
        }

        try:
            async with httpx.AsyncClient(timeout=settings.supabase_timeout_seconds) as client:
                response = await client.post(
                    f"{base_url}/storage/v1/object/{bucket}/{file_name}",
                    content=content,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Storage upload failed") from exc

        if response.is_error:
            raise HTTPException(status_code=502, detail=f"Storage upload failed: {response.text}")

        return f"{base_url}/storage/v1/object/public/{bucket}/{file_name}"
