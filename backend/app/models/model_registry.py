from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ModelRegistry(Base):
    """本地模型注册表。

    文件存在 models/ 目录里；注册表保存模型类型、family、输入尺寸、类别/关键点元数据。
    这样前端配置摄像头时不用手写 detector_config。
    """

    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    model_type: Mapped[str] = mapped_column(String(64), default="yolo")
    model_family: Mapped[str] = mapped_column(String(64), default="auto")
    input_size: Mapped[int] = mapped_column(Integer, default=640)
    class_count: Mapped[int] = mapped_column(Integer, default=1)
    num_keypoints: Mapped[int] = mapped_column(Integer, default=0)
    labels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
