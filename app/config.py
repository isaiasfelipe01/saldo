import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    supabase_url: str
    supabase_key: str
    host: str = "0.0.0.0"
    port: int = 8000
    pluggy_client_id: str = ""
    pluggy_client_secret: str = ""
    pluggy_webhook_url: str = ""
    pluggy_webhook_secret: str = ""
    pluggy_include_sandbox: bool = False

    # Read from .env file if it exists, otherwise fall back to environment variables
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
