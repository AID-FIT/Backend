import hashlib
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import HTTPException, UploadFile

from app.core.config import settings


class StorageService:
    async def save_upload(self, file: UploadFile) -> dict:
        suffix = Path(file.filename or "").suffix or ".bin"
        content = await file.read()
        content_type = file.content_type or "application/octet-stream"

        # 내용 해시를 파일명으로 삼으면 같은 이미지는 항상 같은 경로가 되어,
        # 재업로드 없이 기존 객체를 그대로 가리킬 수 있다.
        content_hash = hashlib.sha256(content).hexdigest()
        file_name = f"{content_hash}{suffix}"

        # 서버리스에서는 로컬 디스크가 요청 간 유지되지 않으므로 Supabase Storage를 쓴다.
        if settings.supabase_storage_enabled:
            image_url = await self._store_in_supabase(file_name, content, content_type)
        else:
            target = settings.upload_dir / file_name
            if not target.exists():
                target.write_bytes(content)
            image_url = f"{settings.public_base_url}/uploads/{file_name}"

        return {
            # Postgres가 대시 형식으로 정규화하므로, 신규 응답도 같은 표기로 맞춘다.
            "id": str(uuid4()),
            "image_url": image_url,
            "content_type": content_type,
            "content_hash": content_hash,
        }

    async def delete_by_url(self, image_url: str) -> None:
        """저장된 객체를 지운다. 이미 없으면 조용히 지나간다."""
        file_name = image_url.rstrip("/").rsplit("/", 1)[-1]
        if not file_name:
            return

        if not settings.supabase_storage_enabled:
            target = settings.upload_dir / file_name
            target.unlink(missing_ok=True)
            return

        base_url = settings.supabase_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=settings.supabase_timeout_seconds) as client:
                await client.delete(
                    f"{base_url}/storage/v1/object/{settings.supabase_storage_bucket}/{file_name}",
                    headers={
                        "apikey": settings.supabase_service_key,
                        "Authorization": f"Bearer {settings.supabase_service_key}",
                    },
                )
        except httpx.HTTPError:
            # 원본 삭제 실패로 사용자의 삭제 요청 자체를 실패시키지는 않는다.
            return

    async def _store_in_supabase(self, file_name: str, content: bytes, content_type: str) -> str:
        base_url = settings.supabase_url.rstrip("/")
        bucket = settings.supabase_storage_bucket
        object_url = f"{base_url}/storage/v1/object/{bucket}/{file_name}"
        public_url = f"{base_url}/storage/v1/object/public/{bucket}/{file_name}"

        # 신형 sb_secret_ 키는 apikey 헤더로 인증한다. 구형 service_role JWT도 두 헤더를 함께 받는다.
        auth_headers = {
            "apikey": settings.supabase_service_key,
            "Authorization": f"Bearer {settings.supabase_service_key}",
        }

        try:
            async with httpx.AsyncClient(timeout=settings.supabase_timeout_seconds) as client:
                # 이미 같은 내용이 올라가 있으면 전송을 건너뛴다.
                head = await client.head(public_url)
                if head.is_success:
                    return public_url

                response = await client.post(
                    object_url,
                    content=content,
                    headers={**auth_headers, "Content-Type": content_type},
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Storage upload failed") from exc

        # 동시 업로드로 그사이 같은 객체가 생겼다면 그것을 그대로 쓴다.
        if response.status_code == 409:
            return public_url

        if response.is_error:
            raise HTTPException(status_code=502, detail=f"Storage upload failed: {response.text}")

        return public_url
