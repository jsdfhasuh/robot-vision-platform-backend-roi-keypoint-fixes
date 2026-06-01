from datetime import datetime
from pydantic import BaseModel, Field

class CameraBase(BaseModel):
    name: str
    rtsp_url: str
    location: str | None = ""
    # 前端兼容字段：数据库暂不单独建列，保存时会落到 location / detector_config.frontend_meta。
    area: str | None = None
    line: str | None = None
    robot_id: str | None = None
    robot_name: str | None = None
    enabled: bool = True
    fps_limit: int = Field(default=3, ge=1, le=30)
    roi: list[int] | None = None
    detector_type: str = "motion"
    detector_config: dict | None = None
    motion_threshold: float = 5.0
    stop_seconds: int = Field(default=30, ge=1)

class CameraCreate(CameraBase):
    pass

class CameraUpdate(BaseModel):
    name: str | None = None
    rtsp_url: str | None = None
    location: str | None = None
    area: str | None = None
    line: str | None = None
    robot_id: str | None = None
    robot_name: str | None = None
    enabled: bool | None = None
    fps_limit: int | None = None
    roi: list[int] | None = None
    detector_type: str | None = None
    detector_config: dict | None = None
    motion_threshold: float | None = None
    stop_seconds: int | None = None

class CameraOut(CameraBase):
    id: int
    config_version: int = 1
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
