from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        repr=False,
        populate_by_name=True,
    )

    base_dir: Path
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    openai_api_key: SecretStr | None = Field(default=None, repr=False)
    gemini_api_key: SecretStr | None = Field(default=None, repr=False)
    pexels_api_key: SecretStr | None = Field(default=None, repr=False)
    meta_instagram_access_token: SecretStr | None = Field(default=None, repr=False)
    meta_instagram_user_id: str | None = None
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = Field(default=None, repr=False)
    meta_public_base_url: str | None = None
    script_model: str = "gpt-5.6-terra"
    gemini_model: str = "gemini-3.6-flash"
    gemini_tts_model: str = "gemini-3.1-flash-tts-preview"
    gemini_tts_voice: str = "Kore"
    tts_model: str = Field(
        default="gpt-4o-mini-tts",
        validation_alias=AliasChoices("OPENAI_TTS_MODEL", "TTS_MODEL"),
    )
    tts_voice: str = "coral"
    monthly_warning_krw: int = 50_000
    monthly_limit_krw: int = 100_000
    offline: bool = Field(
        default=False,
        validation_alias=AliasChoices("CLIPFLOW_OFFLINE", "OFFLINE"),
    )
    brand_name: str = "샘플업체"
    dog_name: str = "줄라이"
    brand_site_url: str = "https://naver.me/GCJXXzpR"

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "clipflow.sqlite3"

    @classmethod
    def load(cls, base_dir: Path) -> "Settings":
        root = base_dir.resolve()
        return cls(
            base_dir=root,
            _env_file=root / ".env",
            _env_file_encoding="utf-8",
        )
