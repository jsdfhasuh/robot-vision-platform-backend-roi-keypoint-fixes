from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DetectionRule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(1024), default="")
    supported_detector_types: Mapped[list] = mapped_column(JSON, default=list)
    rule_config: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DetectionTask(Base):
    __tablename__ = "detection_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    camera_id: Mapped[int] = mapped_column(Integer, ForeignKey("cameras.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    detector_type: Mapped[str] = mapped_column(String(64), default="motion", index=True)
    roi: Mapped[list | None] = mapped_column(JSON, nullable=True)
    detector_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tracker_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rule_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("rules.id"), nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    fps_limit: Mapped[int] = mapped_column(Integer, default=3)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TaskStatus(Base):
    __tablename__ = "task_status"

    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("detection_tasks.id"), primary_key=True)
    camera_id: Mapped[int] = mapped_column(Integer, ForeignKey("cameras.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="UNKNOWN", index=True)
    last_frame_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_motion_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_detect_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(String(512), default="")
    reason_code: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CameraStreamStatus(Base):
    __tablename__ = "camera_stream_status"

    camera_id: Mapped[int] = mapped_column(Integer, ForeignKey("cameras.id"), primary_key=True)
    stream_status: Mapped[str] = mapped_column(String(32), default="OFFLINE", index=True)
    last_frame_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(String(512), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
