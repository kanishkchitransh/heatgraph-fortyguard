from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    fortyguard_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    database_url: str = "sqlite:///./heatgraph.db"
    fortyguard_base_url: str = "https://api.fortyguard.com"
    # Poll interval and max wait for async FortyGuard jobs
    poll_interval_seconds: float = 2.0
    poll_max_seconds: float = 120.0
    # Set to True while waiting for API key activation
    mock_mode: bool = False


settings = Settings()
