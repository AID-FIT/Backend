from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "AID-FIT Backend"
    environment: str = "local"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://aidfit:aidfit@localhost:5432/aidfit"
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7
    local_upload_dir: str = "uploads"
    public_base_url: str = "http://localhost:8000"
    cors_origins_raw: str = Field(
        default="http://localhost:8081,http://localhost:19006,http://localhost:3000",
        alias="CORS_ORIGINS",
    )
    use_mock_ai: bool = True
    google_client_ids_raw: str = Field(default="", alias="GOOGLE_CLIENT_IDS")
    apple_client_ids_raw: str = Field(default="", alias="APPLE_CLIENT_IDS")
    auth_allow_unverified_tokens: bool = False

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @property
    def upload_dir(self) -> Path:
        return Path(self.local_upload_dir)

    @property
    def google_client_ids(self) -> list[str]:
        return [client_id.strip() for client_id in self.google_client_ids_raw.split(",") if client_id.strip()]

    @property
    def apple_client_ids(self) -> list[str]:
        return [client_id.strip() for client_id in self.apple_client_ids_raw.split(",") if client_id.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
