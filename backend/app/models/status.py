from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class CameraStatus(Base):
    __tablename__ = "camera_status"

    camera_id: Mapped[int] = mapped_column(Integer, ForeignKey("cameras.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    last_frame_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_motion_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(String(512), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
