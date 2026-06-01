from datetime import datetime
from sqlalchemy import String, Boolean, Integer, Float, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    rtsp_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    fps_limit: Mapped[int] = mapped_column(Integer, default=3)
    roi: Mapped[list | None] = mapped_column(JSON, nullable=True)
    detector_type: Mapped[str] = mapped_column(String(64), default="motion")
    detector_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    motion_threshold: Mapped[float] = mapped_column(Float, default=5.0)
    stop_seconds: Mapped[int] = mapped_column(Integer, default=30)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
