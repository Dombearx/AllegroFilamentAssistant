from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    allegro_client_id: str
    allegro_client_secret: str
    allegro_env: str = "sandbox"

    # If set, skip category discovery and use this ID directly.
    # Find it once with `uv run allegro-smoke-test --discover-category`, then pin it here.
    allegro_filament_category_id: str | None = None

    max_offers: int = 120
    image_concurrency: int = 8
    delta_e_threshold: float = 10.0
    cache_dir: str = ".cache"

    @field_validator("allegro_env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        if v not in ("prod", "sandbox"):
            raise ValueError("allegro_env must be 'prod' or 'sandbox'")
        return v

    @property
    def auth_base(self) -> str:
        if self.allegro_env == "sandbox":
            return "https://allegro.pl.allegrosandbox.pl"
        return "https://allegro.pl"

    @property
    def api_base(self) -> str:
        if self.allegro_env == "sandbox":
            return "https://api.allegro.pl.allegrosandbox.pl"
        return "https://api.allegro.pl"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
