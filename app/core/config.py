from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AID-FIT Backend"
    environment: str = "local"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://aidfit:aidfit@localhost:5432/aidfit"
    db_use_pgbouncer: bool = False
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7
    local_upload_dir: str = "uploads"
    public_base_url: str = "http://localhost:8000"
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_storage_bucket: str = "uploads"
    supabase_timeout_seconds: float = 30.0
    cors_origins_raw: str = Field(
        default="http://localhost:8081,http://localhost:19006,http://localhost:3000",
        alias="CORS_ORIGINS",
    )
    use_mock_ai: bool = True
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    # Gemini 3 models think before answering, so 30s times out on real requests.
    gemini_timeout_seconds: float = 60.0
    vlm_model: str = ""
    vlm_timeout_seconds: float = 30.0
    vlm_max_concurrency: int = 4
    vlm_max_image_bytes: int = 8 * 1024 * 1024
    vlm_max_items_per_image: int = 8
    rag_candidate_cache_ttl_seconds: int = 15 * 60
    google_client_ids_raw: str = Field(default="", alias="GOOGLE_CLIENT_IDS")
    apple_client_ids_raw: str = Field(default="", alias="APPLE_CLIENT_IDS")
    auth_allow_unverified_tokens: bool = False

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @property
    def vlm_model_name(self) -> str:
        # Vision falls back to the shared Gemini model unless it is pinned separately.
        return self.vlm_model or self.gemini_model

    @property
    def upload_dir(self) -> Path:
        return Path(self.local_upload_dir)

    # 두 값이 모두 있을 때만 원격 스토리지를 쓰고, 없으면 로컬 디스크로 떨어진다.
    @property
    def supabase_storage_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)

    @property
    def google_client_ids(self) -> list[str]:
        return [client_id.strip() for client_id in self.google_client_ids_raw.split(",") if client_id.strip()]

    @property
    def apple_client_ids(self) -> list[str]:
        return [client_id.strip() for client_id in self.apple_client_ids_raw.split(",") if client_id.strip()]


# 싱글톤 효과 - 처음 만든 객체 재사용 (lru_cache)
@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
