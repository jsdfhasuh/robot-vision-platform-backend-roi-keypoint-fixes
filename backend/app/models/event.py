from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models.detection_task import DetectionTask  # noqa: F401 - register FK target with SQLAlchemy metadata


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    camera_id: Mapped[int] = mapped_column(Integer, ForeignKey("cameras.id"), index=True)
    task_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("detection_tasks.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(32), default="STOPPED")
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    start_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 原始截图、标注截图、恢复截图分开存，方便事件复盘。
    snapshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    annotated_snapshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    recovery_snapshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    recovery_annotated_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # 预留短视频路径。第一版先不强制保存视频。
    clip_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # 事件解释信息：为什么打开/恢复、当时规则和 tracker 指标是什么。
    reason: Mapped[str] = mapped_column(String(1024), default="")
    rule_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    detector_type: Mapped[str] = mapped_column(String(64), default="")

    handled: Mapped[bool] = mapped_column(Boolean, default=False)
    false_alarm: Mapped[bool] = mapped_column(Boolean, default=False)
    remark: Mapped[str] = mapped_column(String(512), default="")
