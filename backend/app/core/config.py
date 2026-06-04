from pathlib import Path

from pydantic_settings import BaseSettings


APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORAGE_DIR = APP_ROOT / "data"
DEFAULT_MODEL_DIR = APP_ROOT / "models"
DEFAULT_DB_PATH = DEFAULT_STORAGE_DIR / "db" / "app.db"


class Settings(BaseSettings):
    app_name: str = "Robot Vision Platform"
    database_url: str = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"
    storage_dir: str = str(DEFAULT_STORAGE_DIR)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080"
    log_level: str = "INFO"
    log_dir: str = str(DEFAULT_STORAGE_DIR / "logs")
    log_backup_count: int = 14
    model_dir: str = str(DEFAULT_MODEL_DIR)
    alert_enabled: bool = False
    alert_cooldown_seconds: int = 300
    notify_recovery: bool = True
    webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # 事件复盘采样：只在事件处于 OPEN 状态时周期性保存关键帧。
    event_frame_sample_seconds: int = 10
    event_frame_max_per_event: int = 60

    # 数据维护默认策略。
    cleanup_sample_frame_days: int = 30
    cleanup_orphan_file_days: int = 14
    backup_keep_count: int = 10

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

settings = Settings()
