from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    redis_url: str
    encryption_key: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7
    telegram_bot_token: str
    telegram_bot_username: str
    admin_telegram_chat_id: int
    docker_socket: str
    temp_download_path: str
    max_video_size_mb: float = 1024.0
    headless: bool = True
    browser_service_url: str = "http://browser_service:8000"
    browser_novnc_url: str = "http://localhost:6081/vnc.html"

    @property
    def secret_key(self) -> str:
        return self.jwt_secret_key

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


# Singleton экземпляр
settings = Settings()
DATABASE_URL = settings.database_url
