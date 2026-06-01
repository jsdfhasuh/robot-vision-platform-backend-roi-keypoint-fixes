from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class EventFrame(Base):
    """事件关键帧，用于内网事件复盘。

    一个事件可以有多张关键帧：open、recover、manual、debug 等。
    第一版先保存图片路径和规则明细，不强制保存视频。
    """

    __tablename__ = "event_frames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), index=True)
    camera_id: Mapped[int] = mapped_column(Integer, ForeignKey("cameras.id"), index=True)
    frame_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    frame_type: Mapped[str] = mapped_column(String(64), default="open")
    status: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    annotated_image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    detector_type: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(String(1024), default="")
    rule_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
